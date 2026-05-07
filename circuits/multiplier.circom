pragma circom 2.0.0;

// Trivial proof-of-knowledge circuit used to validate the ZK toolchain.
//
// Public:  c
// Private: a, b
// Claim:   the prover knows a, b such that a * b = c
//
// This circuit deliberately has no cryptographic content — its only job is
// to compile, run trusted setup, generate a proof, and verify. Once the
// toolchain is validated end-to-end (PR #10), the real MiMC + Merkle-tree
// withdraw circuit replaces it (PR #11), and the verifier is wired into
// MockTornado.sol (PR #12).

template Multiplier() {
    signal input a;        // private
    signal input b;        // private
    signal input c;        // public — declared in main below
    c === a * b;
}

component main {public [c]} = Multiplier();
