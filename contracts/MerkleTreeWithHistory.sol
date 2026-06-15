// SPDX-License-Identifier: MIT
// ============================================================================
// ATTRIBUTION
// Adapted for research use from the Tornado Cash MerkleTreeWithHistory
// implementation (https://github.com/tornadocash/tornado-core, MIT License).
// The algorithmic structure (append-only Merkle tree with bounded root
// history, MiMC-Feistel hashLeftRight, zero-value precomputed subtree
// digests) is preserved unchanged because it defines the cryptographic
// contract that the off-chain prover commits to. Modifications are limited
// to comments and Solidity 0.8+ idioms; no semantic changes to the tree
// or its invariants.
// ============================================================================
pragma solidity ^0.8.20;

import {IHasher} from "./IHasher.sol";

/// @title MerkleTreeWithHistory — research artifact
/// @notice Tornado-Cash-style append-only Merkle tree with bounded root
///         history. Leaves are inserted via `insert(bytes32)`; each
///         insertion incrementally re-hashes the path from the new leaf
///         to the root. The last `ROOT_HISTORY_SIZE` roots are kept so
///         a withdrawal proof generated against an older root still
///         verifies even after concurrent deposits change the latest root.
///
///         hashLeftRight uses two MiMC Feistel calls to compute
///         MiMCSponge(2, 220, 1)([left, right], k=0) — the same hash
///         function our withdraw circuit verifies against. See
///         contracts/IHasher.sol for the underlying MiMC contract shape.
///
/// @dev    Local fork only. Adapted from tornado-core/contracts/
///         MerkleTreeWithHistory.sol (MIT) with two changes:
///           1. IHasher.MiMCSponge takes (xL, xR, k) instead of (xL, xR);
///              we always pass k=0.
///           2. zeros[] is computed dynamically in the constructor rather
///              than hardcoded — saves us from precomputing depth-specific
///              values offline and lets the same contract work at any
///              valid depth.
contract MerkleTreeWithHistory {
    /// @notice bn254 scalar field — same as circomlib's snark field.
    uint256 public constant FIELD_SIZE =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    /// @notice Number of historical roots kept for withdrawal verification.
    uint32 public constant ROOT_HISTORY_SIZE = 30;

    uint32 public immutable levels;
    IHasher public immutable hasher;

    /// @notice zeros[i] = root of an all-zero subtree of depth i.
    bytes32[] public zeros;

    /// @notice filledSubtrees[i] = leftmost-known-filled subtree at depth i.
    bytes32[] public filledSubtrees;

    /// @notice Bounded ring buffer of historical roots.
    bytes32[ROOT_HISTORY_SIZE] public roots;
    uint32 public currentRootIndex = 0;
    uint32 public nextIndex = 0;

    event LeafInserted(bytes32 indexed commitment, uint32 leafIndex, uint256 timestamp);

    constructor(uint32 _levels, IHasher _hasher) {
        require(_levels > 0, "levels must be > 0");
        require(_levels < 32, "levels must be < 32");
        levels = _levels;
        hasher = _hasher;

        // Initialise zeros[] and filledSubtrees[] using the empty leaf = 0.
        // Each level's zero is the hash of two zeros at the level below.
        bytes32 currentZero = bytes32(0);
        zeros.push(currentZero);
        for (uint32 i = 0; i < _levels; i++) {
            filledSubtrees.push(currentZero);
            currentZero = hashLeftRight(_hasher, currentZero, currentZero);
            zeros.push(currentZero);
        }

        // The initial root is the empty-tree root (every leaf is zero).
        roots[0] = currentZero;
    }

    /// @notice Hash two field elements via the MiMC contract using the
    ///         2-input sponge pattern (matches MiMCSponge(2, 220, 1) k=0).
    /// @dev    `view` rather than `pure` because Solidity 0.8's strict
    ///         purity check forbids external calls from `pure` functions
    ///         (the call could modify state of the callee in principle).
    function hashLeftRight(IHasher _hasher, bytes32 _left, bytes32 _right)
        public view returns (bytes32)
    {
        require(uint256(_left) < FIELD_SIZE, "_left must be in field");
        require(uint256(_right) < FIELD_SIZE, "_right must be in field");
        uint256 R = uint256(_left);
        uint256 C = 0;
        (R, C) = _hasher.MiMCSponge(R, C, 0);
        R = addmod(R, uint256(_right), FIELD_SIZE);
        (R, C) = _hasher.MiMCSponge(R, C, 0);
        return bytes32(R);
    }

    /// @notice Append a new leaf. Returns the leaf's index in the tree.
    function insert(bytes32 _leaf) public returns (uint32 leafIndex) {
        leafIndex = nextIndex;
        require(leafIndex < uint32(2) ** levels, "Tree is full");

        uint32 currentIndex = leafIndex;
        bytes32 currentLevelHash = _leaf;

        for (uint32 i = 0; i < levels; i++) {
            bytes32 left;
            bytes32 right;
            if (currentIndex % 2 == 0) {
                // currentIndex is a left child; its sibling is the level's
                // zero subtree (no leaf inserted on that side yet).
                left = currentLevelHash;
                right = zeros[i];
                // Cache this newly-filled subtree for future right-child
                // insertions at the same level.
                filledSubtrees[i] = currentLevelHash;
            } else {
                // currentIndex is a right child; its sibling is the most
                // recently filled subtree at this level.
                left = filledSubtrees[i];
                right = currentLevelHash;
            }
            currentLevelHash = hashLeftRight(hasher, left, right);
            currentIndex /= 2;
        }

        currentRootIndex = (currentRootIndex + 1) % ROOT_HISTORY_SIZE;
        roots[currentRootIndex] = currentLevelHash;
        nextIndex = leafIndex + 1;

        emit LeafInserted(_leaf, leafIndex, block.timestamp);
    }

    /// @notice Whether `_root` matches any of the last ROOT_HISTORY_SIZE roots.
    ///         Used by the withdraw flow so a proof against an older root
    ///         still verifies after concurrent deposits.
    function isKnownRoot(bytes32 _root) public view returns (bool) {
        if (_root == 0) return false;
        uint32 i = currentRootIndex;
        do {
            if (_root == roots[i]) return true;
            if (i == 0) i = ROOT_HISTORY_SIZE;
            i--;
        } while (i != currentRootIndex);
        return false;
    }

    /// @notice The most recently inserted root.
    function getLastRoot() public view returns (bytes32) {
        return roots[currentRootIndex];
    }
}
