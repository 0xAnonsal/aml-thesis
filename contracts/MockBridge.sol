// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

/// @title MockBridge — research artifact
/// @notice One-side primitive for a lock-and-mint cross-chain bridge. This
///         contract sits on the Ethereum simulator. The launderer locks
///         USDT here when "sending to Tron", and an operator releases USDT
///         here when funds arrive back from the Tron simulator. The Tron
///         simulator (week 4) mirrors this contract on its side; together
///         they are the two halves of a cross-chain transfer.
///
///         Purposely simplified: no validator multisig, no on-chain
///         signature verification. The `operator` role stands in for the
///         real bridge's off-chain relayer / validator network.
///         AML observable signals — Locked event, Released event, amounts,
///         addresses, timestamps, sequence numbers — match real bridges
///         (Wormhole, Stargate, deBridge) closely enough for detection
///         research. Defender's coordinator correlates Locked-on-ETH events
///         with mirror Lock events on Tron via the destinationAddress and
///         amount + timestamp window.
/// @dev    Local fork only. NEVER deploy on a real chain.
contract MockBridge {
    IERC20 public immutable usdt;
    address public immutable operator;

    /// @notice Monotonic counter of lock events on this chain.
    uint256 public lockSequence;
    /// @notice Foreign-chain sequence numbers already processed (replay guard).
    mapping(uint256 => bool) public foreignSequenceProcessed;

    event Locked(
        uint256 indexed sequence,
        address indexed sender,
        bytes32 indexed destinationAddress,
        uint256 amount,
        uint256 timestamp
    );
    event Released(
        uint256 indexed foreignSequence,
        address indexed recipient,
        uint256 amount,
        uint256 timestamp
    );

    modifier onlyOperator() {
        require(msg.sender == operator, "Bridge: only operator");
        _;
    }

    constructor(address _usdt) {
        require(_usdt != address(0), "Bridge: zero usdt");
        usdt = IERC20(_usdt);
        operator = msg.sender;
    }

    /// @notice Lock USDT on this chain for transfer to the foreign chain.
    /// @param amount USDT amount (6 decimals).
    /// @param destinationAddress encoded recipient on the destination chain
    ///        (e.g. Tron base58 address right-padded into bytes32).
    /// @return sequence the lock event sequence number.
    function lockUSDT(uint256 amount, bytes32 destinationAddress) external returns (uint256 sequence) {
        require(amount > 0, "Bridge: zero amount");
        require(destinationAddress != bytes32(0), "Bridge: zero destination");
        require(usdt.transferFrom(msg.sender, address(this), amount), "Bridge: USDT transfer");
        lockSequence += 1;
        sequence = lockSequence;
        emit Locked(sequence, msg.sender, destinationAddress, amount, block.timestamp);
    }

    /// @notice Release USDT to `recipient` in response to a lock observed on
    ///         the foreign chain. Operator-only.
    function releaseUSDT(
        uint256 foreignSequence,
        address recipient,
        uint256 amount
    ) external onlyOperator {
        require(recipient != address(0), "Bridge: zero recipient");
        require(amount > 0, "Bridge: zero amount");
        require(!foreignSequenceProcessed[foreignSequence], "Bridge: already processed");
        foreignSequenceProcessed[foreignSequence] = true;
        require(usdt.transfer(recipient, amount), "Bridge: USDT transfer");
        emit Released(foreignSequence, recipient, amount, block.timestamp);
    }

    function lockedBalance() external view returns (uint256) {
        return usdt.balanceOf(address(this));
    }
}
