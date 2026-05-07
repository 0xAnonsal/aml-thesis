// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.20;

/// @title MockUSDT — research artifact
/// @notice Minimal USDT-shaped ERC-20 (6 decimals) for AML simulation on a
///         local Anvil fork. NO RELATION to the real Tether contract. The
///         `mint` function is permissionless by design — convenient for
///         seeding the launderer's wallets, catastrophic on a real chain.
///         Deploy ONLY on local test chains. See ROADMAP §6 (ethics).
/// @dev    Hand-written to avoid an OpenZeppelin git submodule for one
///         contract. Standard ERC-20 interface, no fee-on-transfer, no
///         blacklist (the real Tether has both — we do not exercise either).
contract MockUSDT {
    string public constant name = "Mock USDT";
    string public constant symbol = "USDT";
    uint8 public constant decimals = 6;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function transfer(address to, uint256 value) external returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= value, "MockUSDT: insufficient allowance");
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - value;
        }
        _transfer(from, to, value);
        return true;
    }

    /// @notice Permissionless mint — research convenience only.
    function mint(address to, uint256 value) external {
        require(to != address(0), "MockUSDT: mint to zero");
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function _transfer(address from, address to, uint256 value) internal {
        require(to != address(0), "MockUSDT: transfer to zero");
        require(balanceOf[from] >= value, "MockUSDT: insufficient balance");
        unchecked {
            balanceOf[from] -= value;
            balanceOf[to] += value;
        }
        emit Transfer(from, to, value);
    }
}
