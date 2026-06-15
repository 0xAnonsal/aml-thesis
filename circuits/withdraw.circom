// ============================================================================
// ATTRIBUTION
// Research-purpose adaptation of the Tornado Cash withdraw circuit
// (https://github.com/tornadocash/tornado-core, MIT License). The witness
// structure (nullifier + secret commitment, Merkle path inclusion proof,
// nullifier hash derivation, recipient/fee/refund signal binding) follows
// the canonical Tornado Cash design unchanged so that the proof system has
// the same soundness and zero-knowledge properties as the real system.
// Constants (LEVELS=10) and zero-public-signals (fee=refund=0) reflect the
// no-relayer single-denomination variant used in this simulation.
// MiMCSponge primitive is consumed from upstream circomlib (MIT License).
// ============================================================================
pragma circom 2.0.0;

include "circomlib/circuits/mimcsponge.circom";

// Tornado-Cash-style Groth16 withdraw circuit.
//
// Public inputs (visible on-chain):
//   root            Merkle root that contains the depositor's commitment
//   nullifierHash   MiMC(nullifier) — recorded by the contract on withdraw to prevent double-spend
//   recipient       address that receives the withdrawn ETH
//   fee             relayer fee (set to 0 in our research mock — no relayer)
//   refund          relayer refund (set to 0 in our research mock)
//
// Private witness (only the prover sees these):
//   nullifier, secret           the deposit's note
//   pathElements[LEVELS]        sibling hashes along the Merkle path
//   pathIndices[LEVELS]         left/right bits along the Merkle path
//
// Constraints enforced by the circuit:
//   1. commitment      = MiMCSponge(nullifier, secret, k=0)
//   2. nullifierHash  === MiMCSponge(nullifier, k=0)        (binds the nullifier)
//   3. root            === MerkleRoot(commitment, pathElements, pathIndices)
//                                                            (proves the commitment was deposited)
//   4. recipient*recipient, fee*fee, refund*refund — bind the public inputs
//      to the proof so it can't be replayed against a different recipient
//      (Tornado convention; constraint 0 would also work, this is what the
//      original repo uses).
//
// What this circuit does NOT reveal: which commitment was withdrawn. The
// proof certifies "I know SOME (nullifier, secret) whose commitment is in
// the tree at root", without exposing which leaf. That's the privacy
// property that distinguishes the real ZK mixer from the keccak-mock we
// shipped in week 3.3.

template CommitmentHasher() {
    signal input nullifier;
    signal input secret;
    signal output commitment;
    signal output nullifierHash;

    // commitment = MiMCSponge(nullifier, secret, k=0, nOuts=1)
    component commitmentHasher = MiMCSponge(2, 220, 1);
    commitmentHasher.ins[0] <== nullifier;
    commitmentHasher.ins[1] <== secret;
    commitmentHasher.k <== 0;
    commitment <== commitmentHasher.outs[0];

    // nullifierHash = MiMCSponge(nullifier, k=0, nOuts=1)
    component nullifierHasher = MiMCSponge(1, 220, 1);
    nullifierHasher.ins[0] <== nullifier;
    nullifierHasher.k <== 0;
    nullifierHash <== nullifierHasher.outs[0];
}

// Selects (in[0], in[1]) if s=0 else (in[1], in[0]). Used to put the
// current node and its Merkle sibling in the right order before hashing.
template DualMux() {
    signal input in[2];
    signal input s;
    signal output out[2];

    s * (1 - s) === 0;   // s must be a bit
    out[0] <== (in[1] - in[0]) * s + in[0];
    out[1] <== (in[0] - in[1]) * s + in[1];
}

// Verifies that `leaf` is at position `pathIndices` in a Merkle tree with
// the given `pathElements` siblings, producing `root`.
template MerkleTreeChecker(levels) {
    signal input leaf;
    signal input root;
    signal input pathElements[levels];
    signal input pathIndices[levels];

    component selectors[levels];
    component hashers[levels];

    for (var i = 0; i < levels; i++) {
        selectors[i] = DualMux();
        selectors[i].in[0] <== i == 0 ? leaf : hashers[i - 1].outs[0];
        selectors[i].in[1] <== pathElements[i];
        selectors[i].s <== pathIndices[i];

        hashers[i] = MiMCSponge(2, 220, 1);
        hashers[i].ins[0] <== selectors[i].out[0];
        hashers[i].ins[1] <== selectors[i].out[1];
        hashers[i].k <== 0;
    }

    root === hashers[levels - 1].outs[0];
}

template Withdraw(levels) {
    // public
    signal input root;
    signal input nullifierHash;
    signal input recipient;
    signal input fee;
    signal input refund;

    // private
    signal input nullifier;
    signal input secret;
    signal input pathElements[levels];
    signal input pathIndices[levels];

    // 1. Recompute commitment + nullifierHash from the witness
    component hasher = CommitmentHasher();
    hasher.nullifier <== nullifier;
    hasher.secret <== secret;
    hasher.nullifierHash === nullifierHash;

    // 2. Prove the commitment is in the Merkle tree at `root`
    component tree = MerkleTreeChecker(levels);
    tree.leaf <== hasher.commitment;
    tree.root <== root;
    for (var i = 0; i < levels; i++) {
        tree.pathElements[i] <== pathElements[i];
        tree.pathIndices[i] <== pathIndices[i];
    }

    // 3. Bind public inputs to the proof. Without these constraints a valid
    //    proof would still verify after swapping (recipient, fee, refund) at
    //    the contract level — these constraints make those signals part of
    //    the witness so any tamper invalidates the proof.
    signal recipientSquare;
    signal feeSquare;
    signal refundSquare;
    recipientSquare <== recipient * recipient;
    feeSquare <== fee * fee;
    refundSquare <== refund * refund;
}

// Depth-10 tree = 1024 leaves capacity. Real Tornado uses 20 (1M); we use 10
// because (a) thesis experiments don't generate anywhere near 1024 deposits
// per campaign, (b) smaller depth ~= half the constraints which is faster
// to prove and stays comfortably under our pot=14 (16384) trusted setup.
component main {public [root, nullifierHash, recipient, fee, refund]} = Withdraw(10);
