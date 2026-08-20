import {readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
import {fileURLToPath} from "node:url";

const require = createRequire(import.meta.url);
const bundleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const KTX_VALIDATOR_LIMITATIONS = new Set(["VALUE_NOT_IN_LIST", "IMAGE_UNRECOGNIZED_FORMAT"]);

function fail(message) {
	process.stderr.write(`${message}\n`);
	process.exitCode = 1;
}

async function glbExtensions(source) {
	const bytes = await readFile(source);
	if (bytes.length < 20 || bytes.toString("ascii", 0, 4) !== "glTF") return new Set();
	const length = bytes.readUInt32LE(12);
	if (bytes.toString("ascii", 16, 20) !== "JSON" || 20 + length > bytes.length) return new Set();
	const document = JSON.parse(bytes.subarray(20, 20 + length).toString("utf8").trim());
	return new Set([...(document.extensionsUsed || []), ...(document.extensionsRequired || [])]);
}

async function viewerSupportsKtx2() {
	try {
		const capabilities = JSON.parse(await readFile(path.join(bundleRoot, "viewer-capabilities.v1.json"), "utf8"));
		return capabilities?.decoders?.ktx2_basisu === true
			&& capabilities?.supported_extensions?.includes("KHR_texture_basisu");
	} catch {
		return false;
	}
}

function isKnownKtxValidatorLimitation(message) {
	return message?.severity === 0
		&& KTX_VALIDATOR_LIMITATIONS.has(message.code)
		&& /^\/images\/\d+(?:\/|$)/.test(String(message.pointer || ""));
}

async function main() {
	let validator;
	try {
		validator = require("gltf-validator");
	} catch (error) {
		throw new Error(`Der gebündelte Khronos glTF-Validator fehlt: ${error.message}`);
	}
	if (process.argv[2] === "--self-test") {
		if (typeof validator.validateBytes !== "function") {
			throw new Error("Der gebündelte Khronos glTF-Validator stellt validateBytes nicht bereit.");
		}
		process.stdout.write("gltf-validator ready\n");
		return;
	}
	const source = process.argv[2];
	if (typeof source !== "string" || !source || process.argv.length !== 3) {
		throw new Error("Aufruf: validate-glb.mjs <eingabe.glb>");
	}
	const report = await validator.validateBytes(new Uint8Array(await readFile(source)), {
		maxIssues: 4096,
	});
	const extensions = await glbExtensions(source);
	const allowKtxLimitations = extensions.has("KHR_texture_basisu") && await viewerSupportsKtx2();
	const suppressed = allowKtxLimitations
		? (report?.issues?.messages || []).filter(isKnownKtxValidatorLimitation)
		: [];
	const blockingErrors = (report?.issues?.messages || []).filter((message) => message?.severity === 0 && !suppressed.includes(message));
	process.stdout.write(`${JSON.stringify({...report, dronautix_policy: {
		viewer_confirmed_extensions: allowKtxLimitations ? ["KHR_texture_basisu"] : [],
		suppressed_validator_limitations: suppressed,
		blocking_error_count: blockingErrors.length,
	}})}\n`);
	if (blockingErrors.length) {
		process.exitCode = 2;
	}
}

main().catch((error) => fail(error?.message || String(error)));
