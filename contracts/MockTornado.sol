// SPDX-License-Identifier: MIT
// ============================================================================
// ATTRIBUTION
// This contract is a research-purpose adaptation of the Tornado Cash mixer
// architecture (https://github.com/tornadocash/tornado-core, MIT License).
// Modifications for AML research simulation:
//   - Single fixed denomination (no multi-denomination pool routing).
//   - No relayer support (fee and refund hard-coded to 0 in proofs).
//   - Comments expanded to document the Groth16 + Merkle invariants for
//     reviewers from outside the ZK community.
// The cryptographic primitives (Groth16 verifier, MiMC hasher, Merkle tree
// commitment scheme) follow the canonical Tornado Cash design unchanged so
// that the simulation has the same on-chain unlinkability properties as the
// real system that laundering campaigns exploit on mainnet.
// ============================================================================
pragma solidity ^0.8.20;

import {IHasher} from "./IHasher.sol";
import {IVerifier} from "./IVerifier.sol";
import {MerkleTreeWithHistory} from "./MerkleTreeWithHistory.sol";

/// @title MockTornado — research artifact (real ZK mixer)
/// @notice Tornado-Cash-style ZK mixer:
///           1. Depositor commits MiMC(nullifier, secret) and sends DENOMINATION ETH.
///           2. Contract inserts the commitment as a leaf in the Merkle tree
///              (via MerkleTreeWithHistory) and updates the root.
///           3. Depositor (off-chain) generates a Groth16 proof that the
///              commitment is in the tree, against any historical root.
///           4. Anyone can submit the withdraw tx with the proof + recipient.
///              The contract verifies the proof, records nullifierHash to
///              prevent double-spend, and pays DENOMINATION ETH to recipient.
///
///         Privacy property: the proof's public signals are
///         (root, nullifierHash, recipient, fee, refund). The depositor's
///         actual commitment is NOT in the public signals, so an external
///         observer can't directly link a withdrawal to a deposit (until
///         many other deposits accumulate, the anonymity set is the on-chain
///         leaf count — same as real Tornado).
///
///         Differences from the keccak-mock shipped in week 3.3:
///           - Commitment scheme: MiMCSponge(2, 220, 1) instead of keccak256
///           - Withdraw verification: Groth16 proof, no on-chain reveal of
///             secret/nullifier
///           - Replay protection: nullifierHash mapping (same idea, but the
///             hash now matches the in-circuit nullifierHasher)
///
/// @dev    Local fork only. Tornado Cash is OFAC-sanctioned in the US.
contract MockTornado is MerkleTreeWithHistory {
    uint256 public constant DENOMINATION = 1 ether;

    IVerifier public immutable verifier;

    /// @notice Spent nullifierHashes. A withdraw can only succeed once per
    ///         (nullifier).
    mapping(bytes32 => bool) public nullifierHashes;

    /// @notice Set of inserted commitments. Used to reject duplicate deposits.
    mapping(bytes32 => bool) public commitments;

    event Deposit(
        bytes32 indexed commitment,
        uint32 leafIndex,
        address indexed depositor,
        uint256 timestamp
    );
    event Withdrawal(
        address indexed recipient,
        bytes32 nullifierHash,
        uint256 timestamp
    );

    constructor(IVerifier _verifier, IHasher _hasher, uint32 _levels)
        MerkleTreeWithHistory(_levels, _hasher)
    {
        require(address(_verifier) != address(0), "MockTornado: zero verifier");
        verifier = _verifier;
    }

    /// @notice Deposit DENOMINATION ETH and register `_commitment` as a leaf.
    function deposit(bytes32 _commitment) external payable {
        require(msg.value == DENOMINATION, "MockTornado: wrong denomination");
        require(!commitments[_commitment], "MockTornado: duplicate commitment");
        uint32 leafIndex = insert(_commitment);
        commitments[_commitment] = true;
        emit Deposit(_commitment, leafIndex, msg.sender, block.timestamp);
    }

    /// @notice Withdraw DENOMINATION ETH to `_recipient` by proving knowledge
    ///         of (secret, nullifier) such that hash(nullifier, secret) is in
    ///         the Merkle tree at `_root`, and `_nullifierHash = hash(nullifier)`.
    /// @dev    The proof's public signals must match
    ///         [_root, _nullifierHash, _recipient, _fee, _refund] in that
    ///         order. _fee and _refund must be 0 in this research mock —
    ///         no relayer support.
    function withdraw(
        uint[2] calldata _pA,
        uint[2][2] calldata _pB,
        uint[2] calldata _pC,
        bytes32 _root,
        bytes32 _nullifierHash,
        address payable _recipient,
        uint256 _fee,
        uint256 _refund
    ) external {
        require(_recipient != address(0), "MockTornado: zero recipient");
        require(!nullifierHashes[_nullifierHash], "MockTornado: nullifier already used");
        require(isKnownRoot(_root), "MockTornado: unknown root");
        require(_fee == 0 && _refund == 0, "MockTornado: relayer not supported");

        // Public signals order matches the Withdraw circuit:
        //   [root, nullifierHash, recipient, fee, refund]
        uint[5] memory pubSignals = [
            uint(_root),
            uint(_nullifierHash),
            uint(uint160(address(_recipient))),
            _fee,
            _refund
        ];
        require(
            verifier.verifyProof(_pA, _pB, _pC, pubSignals),
            "MockTornado: invalid proof"
        );

        nullifierHashes[_nullifierHash] = true;

        (bool sent, ) = _recipient.call{value: DENOMINATION}("");
        require(sent, "MockTornado: ETH transfer failed");

        emit Withdrawal(_recipient, _nullifierHash, block.timestamp);
    }

    /// @notice Pool balance — should equal DENOMINATION × (deposits − withdrawals).
    function poolBalance() external view returns (uint256) {
        return address(this).balance;
    }

    receive() external payable {
        revert("MockTornado: use deposit()");
    }
}
