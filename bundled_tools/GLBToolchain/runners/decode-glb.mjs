import {access, copyFile, readFile} from "node:fs/promises";
import {spawn} from "node:child_process";
import path from "node:path";
import {fileURLToPath} from "node:url";

const bundleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cli = path.join(bundleRoot, "node_modules", "@gltf-transform", "cli", "bin", "cli.js");
const ktxDirectory = path.join(bundleRoot, "ktx", "bin");
const ktx = path.join(ktxDirectory, "ktx.exe");
const compressedExtensions = new Set([
	"KHR_draco_mesh_compression",
	"EXT_meshopt_compression",
	"KHR_meshopt_compression",
	"KHR_texture_basisu",
]);

function fail(message) {
	process.stderr.write(`${message}\n`);
	process.exitCode = 1;
}

function bundledEnvironment() {
	// glTF-Transform invokes `where` to find ktx. Preserve only Windows'
	// System32 helper and the bundled encoder, never the user's PATH.
	const system32 = process.env.SystemRoot ? path.join(process.env.SystemRoot, "System32") : "";
	const executablePath = [ktxDirectory, system32].filter(Boolean).join(path.delimiter);
	return {...process.env, PATH: executablePath, Path: executablePath};
}

async function execute(argumentsList) {
	await new Promise((resolve, reject) => {
		const child = spawn(process.execPath, [cli, ...argumentsList], {
			cwd: bundleRoot,
			env: bundledEnvironment(),
			stdio: "inherit",
			windowsHide: true,
		});
		child.once("error", reject);
		child.once("exit", (code, signal) => {
			if (code === 0) resolve();
			else reject(new Error(`glTF-Transform beendet (${signal || code}).`));
		});
	});
}

async function executeKtx(argumentsList) {
	await new Promise((resolve, reject) => {
		const child = spawn(ktx, argumentsList, {
			cwd: bundleRoot,
			env: bundledEnvironment(),
			stdio: "inherit",
			windowsHide: true,
		});
		child.once("error", reject);
		child.once("exit", (code, signal) => {
			if (code === 0) resolve();
			else reject(new Error(`KTX beendet (${signal || code}).`));
		});
	});
}

async function extensionNames(source) {
	const bytes = await readFile(source);
	if (bytes.length < 20 || bytes.toString("ascii", 0, 4) !== "glTF") {
		throw new Error("Die Eingabe ist keine GLB-2.0-Datei.");
	}
	const jsonLength = bytes.readUInt32LE(12);
	if (bytes.toString("ascii", 16, 20) !== "JSON" || 20 + jsonLength > bytes.length) {
		throw new Error("Die GLB-JSON-Struktur ist ungültig.");
	}
	const document = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString("utf8").trim());
	return new Set([...(document.extensionsUsed || []), ...(document.extensionsRequired || [])]);
}

async function main() {
	if (process.argv[2] === "--self-test") {
		await access(cli);
		await access(ktx);
		await execute(["--version"]);
		await executeKtx(["--version"]);
		process.stdout.write("compressed GLB decoder ready\n");
		return;
	}
	const args = process.argv.slice(2);
	const [source, destination] = args[0] === "decode" ? [args[2], args[3]] : args;
	if (!source || !destination || (args[0] === "decode" ? args.length !== 4 : args.length !== 2)) {
		throw new Error("Aufruf: decode-glb.mjs [decode <extensions>] <eingabe.glb> <ausgabe.glb>");
	}
	const geometryDecoded = `${destination}.geometry.glb`;
	// glTF-Transform copy is deliberately used here: its Session removes Draco
	// and Meshopt compression instead of a lossy decode/re-encode round-trip.
	await execute(["copy", source, geometryDecoded]);
	const extensions = await extensionNames(geometryDecoded);
	if (extensions.has("KHR_texture_basisu")) {
		await execute(["ktxdecompress", geometryDecoded, destination]);
	} else {
		await copyFile(geometryDecoded, destination);
	}
	const remaining = [...(await extensionNames(destination))].filter((name) => compressedExtensions.has(name));
	if (remaining.length) {
		throw new Error(`Komprimierte Erweiterung nach Decoder verblieben: ${remaining.join(", ")}`);
	}
}

main().catch((error) => fail(error?.message || String(error)));
