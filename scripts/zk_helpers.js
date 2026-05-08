#!/usr/bin/env node
// Off-chain hashing + Merkle tree helpers backed by circomlibjs's MiMCSponge.
// Lets Python tests compute the same values the in-circuit MiMCSponge would
// compute, without reimplementing 220 round constants in Python.
//
// Commands:
//
//   mimc <integer>
//     -> MiMCSponge([integer], k=0, nOuts=1) as a decimal string
//
//   mimc2 <integer1> <integer2>
//     -> MiMCSponge([int1, int2], k=0, nOuts=1) as a decimal string
//
//   prepare-withdraw <secret> <nullifier> <leafIndex> <depth>
//     -> JSON object with everything the withdraw circuit needs as inputs:
//        { commitment, nullifierHash, root, pathElements[], pathIndices[] }
//        Builds a Merkle tree of 2^depth leaves, places `commitment` at
//        leafIndex, fills the rest with 0n, and computes root + path.
//
// All numeric inputs are interpreted as bn254 field elements; numeric
// strings pass through BigInt so values larger than 2^53 are safe.

"use strict";

const { buildMimcSponge } = require("circomlibjs");

// MiMC of two field elements (BigInt in, BigInt out)
function hash2(mimc, a, b) {
    return mimc.F.toObject(mimc.multiHash([a, b], 0n));
}

// MiMC of one field element (BigInt in, BigInt out)
function hash1(mimc, a) {
    return mimc.F.toObject(mimc.multiHash([a], 0n));
}

// Build a depth-`depth` Merkle tree with `leaves` (zero-padded to 2^depth)
// and return root + the inclusion path for `leafIndex`.
function buildTreeAndPath(mimc, leaves, leafIndex, depth) {
    const numLeaves = 1 << depth;
    if (leaves.length > numLeaves) {
        throw new Error(`too many leaves: ${leaves.length} > 2^${depth}`);
    }
    if (leafIndex < 0 || leafIndex >= numLeaves) {
        throw new Error(`leafIndex ${leafIndex} out of range for depth ${depth}`);
    }

    // Pad with zeros up to 2^depth
    const padded = leaves.slice();
    while (padded.length < numLeaves) padded.push(0n);

    let nodes = padded;
    const pathElements = [];
    const pathIndices = [];
    let idx = leafIndex;

    for (let level = 0; level < depth; level++) {
        const isRight = idx & 1;
        const siblingIdx = isRight ? idx - 1 : idx + 1;
        pathElements.push(nodes[siblingIdx]);
        pathIndices.push(BigInt(isRight));

        const next = [];
        for (let i = 0; i < nodes.length; i += 2) {
            next.push(hash2(mimc, nodes[i], nodes[i + 1]));
        }
        nodes = next;
        idx = idx >> 1;
    }

    return { root: nodes[0], pathElements, pathIndices };
}

async function main() {
    const cmd = process.argv[2];
    const args = process.argv.slice(3);

    if (!cmd) {
        console.error("usage: node zk_helpers.js {mimc|mimc2|prepare-withdraw} <args...>");
        process.exit(2);
    }

    const mimc = await buildMimcSponge();

    if (cmd === "mimc") {
        if (args.length !== 1) {
            console.error("usage: node zk_helpers.js mimc <integer>");
            process.exit(2);
        }
        console.log(hash1(mimc, BigInt(args[0])).toString());
        return;
    }

    if (cmd === "mimc2") {
        if (args.length !== 2) {
            console.error("usage: node zk_helpers.js mimc2 <integer1> <integer2>");
            process.exit(2);
        }
        console.log(hash2(mimc, BigInt(args[0]), BigInt(args[1])).toString());
        return;
    }

    if (cmd === "prepare-withdraw") {
        if (args.length !== 4) {
            console.error("usage: node zk_helpers.js prepare-withdraw <secret> <nullifier> <leafIndex> <depth>");
            process.exit(2);
        }
        const secret = BigInt(args[0]);
        const nullifier = BigInt(args[1]);
        const leafIndex = parseInt(args[2], 10);
        const depth = parseInt(args[3], 10);

        // commitment = MiMC(nullifier, secret) — order matches CommitmentHasher in withdraw.circom
        const commitment = hash2(mimc, nullifier, secret);
        const nullifierHash = hash1(mimc, nullifier);

        // Build a tree with this commitment placed at `leafIndex`
        const leaves = new Array(leafIndex).fill(0n);
        leaves.push(commitment);
        const { root, pathElements, pathIndices } = buildTreeAndPath(
            mimc, leaves, leafIndex, depth,
        );

        const out = {
            commitment: commitment.toString(),
            nullifierHash: nullifierHash.toString(),
            root: root.toString(),
            pathElements: pathElements.map((x) => x.toString()),
            pathIndices: pathIndices.map((x) => x.toString()),
        };
        console.log(JSON.stringify(out));
        return;
    }

    console.error(`unknown command: ${cmd}`);
    process.exit(2);
}

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
