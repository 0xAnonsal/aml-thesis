// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IHasher — interface for circomlibjs's auto-generated MiMC contract
/// @notice The MiMCSponge contract from
///         circomlibjs.mimcSpongecontract.createCode("mimcsponge", 220)
///         exposes a single Feistel permutation. Inputs are (xL_in, xR_in, k);
///         outputs are (xL, xR). We always pass k=0 to match the in-circuit
///         MiMCSponge convention (and circomlib's MerkleTree convention).
interface IHasher {
    function MiMCSponge(uint256 xL_in, uint256 xR_in, uint256 k)
        external pure returns (uint256 xL, uint256 xR);
}
