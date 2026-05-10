// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title IVerifier — interface for the auto-generated Groth16 verifier
/// @notice The on-chain verifier comes from
///         `snarkjs zkey export solidityverifier` applied to the withdraw
///         circuit's zkey. snarkjs names the contract `Groth16Verifier` but
///         the function signature is stable: 3 proof components plus the
///         public signals. The signals array length is 5 because Withdraw(10)
///         declares 5 public inputs:
///             [root, nullifierHash, recipient, fee, refund]
interface IVerifier {
    function verifyProof(
        uint[2] calldata _pA,
        uint[2][2] calldata _pB,
        uint[2] calldata _pC,
        uint[5] calldata _pubSignals
    ) external view returns (bool);
}
