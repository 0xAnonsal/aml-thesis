pragma circom 2.0.0;

// Use circomlib's canonical MiMCSponge — same primitive Tornado Cash uses.
// Resolved via the `-l node_modules` flag in scripts/setup_zk.sh.
include "circomlib/circuits/mimcsponge.circom";

// Prove knowledge of `preimage` such that MiMCSponge(preimage, k=0) = expectedHash.
//
// Public:  expectedHash
// Private: preimage
//
// Purpose: validate that circomlib's MiMCSponge integrates cleanly with our
// toolchain (compile + trusted setup + prove + verify) before we use it as
// the hash function inside the real Tornado-style withdraw circuit (PR 4.3).
//
// Why MiMC: Tornado Cash uses MiMC for both leaf commitments and Merkle tree
// node hashes. It's a SNARK-friendly hash — small in-circuit constraint count
// per evaluation, well-studied, and the circomlib reference implementation is
// the de-facto standard for academic ZK work.

template PreimageMiMC() {
    signal input preimage;       // private
    signal input expectedHash;   // public

    component hasher = MiMCSponge(1, 220, 1);  // 1 input, 220 rounds, 1 output
    hasher.ins[0] <== preimage;
    hasher.k <== 0;

    expectedHash === hasher.outs[0];
}

component main {public [expectedHash]} = PreimageMiMC();
