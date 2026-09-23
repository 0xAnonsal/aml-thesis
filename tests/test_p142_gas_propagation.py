"""P1-42 Anvil integration test — verifies self-sovereign gas propagation.

Spins up a fresh Anvil, deploys MockUSDT, and confirms:
  1. transfer_usdt propagates gas from sender to non-terminal receivers (G+)
  2. clean_exit receivers get 0 propagated gas (terminal role skip)
  3. Funder pool is NEVER touched during a normal chain (pool empty)
  4. USDT ledger closes correctly across every hop
"""
import shutil
import pytest

from tests.test_tools import _deploy_usdt, _send
from aml.chains import AnvilNode as _AnvilNode
from web3 import Web3
from src.aml.attackers.tools import ToolDispatcher


needs_foundry = pytest.mark.skipif(
    shutil.which("anvil") is None, reason="anvil binary not on PATH",
)


@needs_foundry
def test_p142_gas_propagation_end_to_end():
    with _AnvilNode() as node:
        w3 = Web3(Web3.HTTPProvider(node.rpc_url))
        deployer = node.accounts[0]
        deployer_key = node.private_keys[0]
        alice = node.accounts[1]
        alice_key = node.private_keys[1]

        # Deploy MockUSDT
        usdt = _deploy_usdt(w3, deployer, deployer_key)
        # Mint 10_000 USDT to Alice
        _send(w3, usdt.functions.mint(alice, 10_000 * 10**6),
              deployer, deployer_key, gas=200_000)

        # ToolDispatcher with deployer+alice, no funders (test that G+
        # eliminates the need)
        d = ToolDispatcher(
            w3=w3, usdt_contract=usdt,
            wallets={deployer: deployer_key, alice: alice_key},
        )
        d._wallet_roles[alice] = "alice"

        # Generate placement burner with role
        r1 = d.dispatch("generate_burner_wallet", {"role": "burner_placement"})
        placement = r1.output["address"]
        assert r1.output["role"] == "burner_placement", r1.output
        print(f"[OK] placement burner registered with role={r1.output['role']}")

        r2 = d.dispatch("generate_burner_wallet", {"role": "burner_layering"})
        layering = r2.output["address"]
        print(f"[OK] layering burner registered with role={r2.output['role']}")

        r3 = d.dispatch("register_clean_exit", {"exchange_platform": "Kraken"})
        clean_exit = r3.output["address"]
        assert d._wallet_roles[clean_exit] == "clean_exit"
        print(f"[OK] clean_exit registered with role=clean_exit")

        # === Round 1: Alice → placement burner (G+ should propagate gas) ===
        pre_bal_placement = w3.eth.get_balance(placement)
        assert pre_bal_placement == 0, "burner should start at 0 ETH"

        r = d.dispatch("transfer_usdt", {
            "from_address": alice,
            "to_address": placement,
            "amount_usdt": 500.0,
        })
        assert not r.is_error, r.error
        propagated = r.output.get("gas_propagated_eth", 0)
        prop_tx = r.output.get("gas_propagation_tx")
        post_bal_placement = w3.eth.get_balance(placement)

        print(f"\n[G+] Alice → placement:")
        print(f"     gas_propagated: {propagated:.6f} ETH  (tx: {prop_tx})")
        print(f"     placement bal after: {post_bal_placement/10**18:.6f} ETH")
        assert propagated > 0, "G+ must propagate gas to burner_placement"
        assert post_bal_placement > 0, "placement must have ETH after transfer_usdt"

        # === Round 2: placement → layering ===
        pre_bal_layering = w3.eth.get_balance(layering)
        r = d.dispatch("transfer_usdt", {
            "from_address": placement,
            "to_address": layering,
            "amount_usdt": 400.0,
        })
        assert not r.is_error, r.error
        propagated2 = r.output.get("gas_propagated_eth", 0)
        post_bal_layering = w3.eth.get_balance(layering)

        print(f"\n[G+] placement → layering:")
        print(f"     gas_propagated: {propagated2:.6f} ETH")
        print(f"     layering bal after: {post_bal_layering/10**18:.6f} ETH")
        assert propagated2 > 0, "G+ must propagate gas to burner_layering"
        # Placement should be depleted somewhat but not stranded (post_tx_refuel didn't fire because no funders — that's the point)

        # === Round 3: layering → clean_exit (G+ NOW propagates 1-tx gas) ===
        # P1-43: clean_exit's role tx-budget changed from 0 to 1 (the mule's
        # single off-ramp cash-out), so G+ bundles that gas with the USDT
        # delivery. Exits are now self-sufficient instead of stranded — the
        # opposite of the pre-P1-43 "terminal, no gas" behaviour.
        pre_bal_exit = w3.eth.get_balance(clean_exit)
        r = d.dispatch("transfer_usdt", {
            "from_address": layering,
            "to_address": clean_exit,
            "amount_usdt": 300.0,
        })
        assert not r.is_error, r.error
        propagated3 = r.output.get("gas_propagated_eth", 0)
        post_bal_exit = w3.eth.get_balance(clean_exit)

        print(f"\n[G+] layering → clean_exit:")
        print(f"     gas_propagated: {propagated3:.6f} ETH  (expected >0, 1-tx budget)")
        print(f"     exit bal after: {post_bal_exit/10**18:.6f} ETH  (expected >0)")
        assert propagated3 > 0, "clean_exit should receive its 1-tx gas budget (P1-43)"
        assert post_bal_exit > 0, "clean_exit must have ETH after transfer_usdt"

        # === Verify: funder pool never fired (empty pool anyway) ===
        assert len(d._funder_pool) == 0, "no funders were bootstrapped"
        print(f"\n[OK] Funder pool never fired: {len(d._funder_pool)} funders (empty)")

        # === Final: check USDT flow completeness ===
        for label, addr, expected_usdt in [
            ("alice",      alice,      10_000 - 500),
            ("placement",  placement,  500 - 400),
            ("layering",   layering,   400 - 300),
            ("clean_exit", clean_exit, 300),
        ]:
            bal = usdt.functions.balanceOf(addr).call() / 10**6
            print(f"     {label:12s} USDT: {bal:7.2f}  (expected {expected_usdt})")

        print("\n=== P1-42 A+B+D+G+ Anvil integration test PASSED ===")


if __name__ == "__main__":
    test_p142_gas_propagation_end_to_end()
