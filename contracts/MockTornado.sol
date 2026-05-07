// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.20;

/// @title MockTornado — research artifact
/// @notice Fixed-denomination ETH mixer (1 ETH per pool entry). Mimics the
///         Tornado Cash deposit/withdraw lifecycle structurally, but WITHOUT
///         the zero-knowledge proof that hides which commitment is being
///         withdrawn. The withdrawer reveals (secret, nullifier) on-chain,
///         which an external observer can hash and match back to a deposit
///         commitment. Real Tornado uses a Groth16 proof to break that link.
///
///         Why this is OK for AML thesis research:
///           - We are studying detection, so the on-chain link is something
///             the defender can plausibly use.
///           - The launderer's *attempted* anonymity behaviour (deposit from
///             one address, withdraw to another, time delay between) is
///             preserved.
///           - The defender's challenge is to detect the laundering pattern
///             across the deposit/withdraw event pair, which is the actual
///             research question.
///
///         For laundering simulation only. NEVER deploy on a real chain.
///         Tornado Cash is OFAC-sanctioned in the United States.
/// @dev    Single-denomination, no Merkle tree (anonymity set = list of
///         commitments stored in a mapping). No relayer.
contract MockTornado {
    uint256 public constant DENOMINATION = 1 ether;

    /// @notice Set of deposited commitments. commitment = keccak256(secret, nullifier).
    mapping(bytes32 => bool) public commitments;
    /// @notice Set of spent nullifiers. Prevents double-withdrawal.
    mapping(bytes32 => bool) public nullifierUsed;

    /// @notice Ordered list of all commitments — for anonymity-set sizing.
    bytes32[] public depositList;

    event Deposit(bytes32 indexed commitment, address indexed depositor, uint256 timestamp);
    event Withdraw(bytes32 indexed nullifier, address indexed recipient, uint256 timestamp);

    /// @notice Deposit exactly DENOMINATION ETH and register `commitment`.
    /// @param commitment off-chain-computed keccak256(secret, nullifier).
    function deposit(bytes32 commitment) external payable {
        require(msg.value == DENOMINATION, "Tornado: wrong denomination");
        require(!commitments[commitment], "Tornado: duplicate commitment");
        commitments[commitment] = true;
        depositList.push(commitment);
        emit Deposit(commitment, msg.sender, block.timestamp);
    }

    /// @notice Withdraw DENOMINATION ETH to `recipient` by revealing the
    ///         (secret, nullifier) preimage of an existing commitment. The
    ///         nullifier is recorded so the same deposit cannot be spent
    ///         twice. The recipient need not be the original depositor —
    ///         this is the laundering primitive.
    function withdraw(bytes32 secret, bytes32 nullifier, address payable recipient) external {
        require(recipient != address(0), "Tornado: zero recipient");
        bytes32 commitment = keccak256(abi.encodePacked(secret, nullifier));
        require(commitments[commitment], "Tornado: unknown commitment");
        require(!nullifierUsed[nullifier], "Tornado: nullifier already used");
        nullifierUsed[nullifier] = true;
        (bool sent, ) = recipient.call{value: DENOMINATION}("");
        require(sent, "Tornado: ETH transfer");
        emit Withdraw(nullifier, recipient, block.timestamp);
    }

    function depositCount() external view returns (uint256) {
        return depositList.length;
    }

    /// @notice Pool balance — should always equal DENOMINATION × (deposits − withdrawals).
    function poolBalance() external view returns (uint256) {
        return address(this).balance;
    }

    receive() external payable {
        revert("Tornado: use deposit()");
    }
}
