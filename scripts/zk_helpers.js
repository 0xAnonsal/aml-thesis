#!/usr/bin/env node
// Off-chain MiMC helper. Wraps circomlibjs's MiMCSponge so Python tests can
// compute the same hash that the in-circuit MiMCSponge would compute, without
// reimplementing 220 round constants in Python.
//
// Usage:
//   node scripts/zk_helpers.js mimc <integer>
//     -> prints MiMCSponge([integer], k=0, nOuts=1) as a decimal string
//
//   node scripts/zk_helpers.js mimc2 <integer1> <integer2>
//     -> prints MiMCSponge([int1, int2], k=0, nOuts=1) as a decimal string
//        (used for Merkle tree node hashing in PR 4.3)
//
// Both inputs are interpreted as bn254 field elements; numeric strings are
// passed through BigInt so they handle values larger than 2^53.

"use strict";

const { buildMimcSponge } = require("circomlibjs");

async function main() {
    const cmd = process.argv[2];
    const args = process.argv.slice(3);

    if (!cmd) {
        console.error("usage: node zk_helpers.js {mimc|mimc2} <args...>");
        process.exit(2);
    }

    const mimc = await buildMimcSponge();

    if (cmd === "mimc") {
        if (args.length !== 1) {
            console.error("usage: node zk_helpers.js mimc <integer>");
            process.exit(2);
        }
        const input = BigInt(args[0]);
        const result = mimc.multiHash([input], 0n);
        console.log(mimc.F.toString(result));
        return;
    }

    if (cmd === "mimc2") {
        if (args.length !== 2) {
            console.error("usage: node zk_helpers.js mimc2 <integer1> <integer2>");
            process.exit(2);
        }
        const a = BigInt(args[0]);
        const b = BigInt(args[1]);
        const result = mimc.multiHash([a, b], 0n);
        console.log(mimc.F.toString(result));
        return;
    }

    console.error(`unknown command: ${cmd}`);
    process.exit(2);
}

main().catch((e) => {
    console.error(e);
    process.exit(1);
});
