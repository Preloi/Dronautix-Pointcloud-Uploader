import {access, mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {spawn} from "node:child_process";
import {tmpdir} from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {NodeIO} from "@gltf-transform/core";
import {ALL_EXTENSIONS, EXTMeshoptCompression, KHRTextureBasisu} from "@gltf-transform/extensions";
import sharp from "sharp";

const bundleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const cli = path.join(bundleRoot, "node_modules", "@gltf-transform", "cli", "bin", "cli.js");
const ktxDirectory = path.join(bundleRoot, "ktx", "bin");
const ktx = path.join(ktxDirectory, "ktx.exe");

function fail(message) {
	process.stderr.write(`${message?.stack || message}\n`);
	process.exitCode = 1;
}

function bundledEnvironment() {
	// The runner intentionally excludes the user's PATH: no global KTX/node
	// executable can be selected by glTF-Transform. Windows' `where.exe` is
	// needed by the CLI solely to discover our bundled ktx.exe.
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
		});
		child.once("error", reject);
		child.once("exit", (code, signal) => {
			if (code === 0) resolve();
			else reject(new Error(`KTX beendet (${signal || code}).`));
		});
	});
}

async function executeKtxWithQualityAudit(argumentsList) {
	const output = await new Promise((resolve, reject) => {
		const child = spawn(ktx, argumentsList, {
			cwd: bundleRoot,
			env: bundledEnvironment(),
			stdio: ["ignore", "pipe", "pipe"],
		});
		let report = "";
		child.stdout.on("data", (chunk) => { report += chunk; });
		child.stderr.on("data", (chunk) => { report += chunk; });
		child.once("error", reject);
		child.once("exit", (code, signal) => {
			if (code === 0) resolve(report);
			else reject(new Error(`KTX beendet (${signal || code}): ${report.trim()}`));
		});
	});
	const levelZero = /Level 0:\s*[\r\n]+\s*PSNR:\s*([0-9.]+)/i.exec(output);
	const psnr = Number(levelZero?.[1]);
	if (!Number.isFinite(psnr) || psnr < MIN_KTX2_PSNR_DB) {
		throw new Error(`E_KTX_QUALITY: ${Number.isFinite(psnr) ? psnr.toFixed(2) : "unbekannt"} dB unterschreitet ${MIN_KTX2_PSNR_DB} dB.`);
	}
}

async function isStaticScene(source) {
	const bytes = await readFile(source);
	if (bytes.length < 20 || bytes.toString("ascii", 0, 4) !== "glTF") return false;
	const jsonLength = bytes.readUInt32LE(12);
	if (bytes.toString("ascii", 16, 20) !== "JSON" || 20 + jsonLength > bytes.length) return false;
	const document = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString("utf8").trim());
	if (Array.isArray(document.animations) && document.animations.length) return false;
	if (Array.isArray(document.skins) && document.skins.length) return false;
	const meshes = Array.isArray(document.meshes) ? document.meshes : [];
	return !meshes.some((mesh) => Array.isArray(mesh?.primitives)
		&& mesh.primitives.some((primitive) => Array.isArray(primitive?.targets) && primitive.targets.length));
}

function glbJson(source) {
	const bytes = readFile(source);
	return bytes.then((contents) => {
		if (contents.length < 20 || contents.toString("ascii", 0, 4) !== "glTF") {
			throw new Error("E_KTX_INPUT: Die Eingabe ist keine GLB-2.0-Datei.");
		}
		const jsonLength = contents.readUInt32LE(12);
		if (contents.toString("ascii", 16, 20) !== "JSON" || 20 + jsonLength > contents.length) {
			throw new Error("E_KTX_INPUT: Die GLB-JSON-Struktur ist ungültig.");
		}
		return JSON.parse(contents.subarray(20, 20 + jsonLength).toString("utf8").trim());
	});
}

const SRGB_TEXTURE_SLOTS = new Set([
	"baseColorTexture", "emissiveTexture", "sheenColorTexture", "specularColorTexture",
]);
const LINEAR_TEXTURE_SLOTS = new Set([
	"metallicRoughnessTexture", "normalTexture", "occlusionTexture", "clearcoatTexture",
	"clearcoatRoughnessTexture", "clearcoatNormalTexture", "sheenRoughnessTexture",
	"specularTexture", "transmissionTexture", "thicknessTexture", "iridescenceTexture",
	"iridescenceThicknessTexture", "anisotropyTexture", "bumpTexture",
]);
const KTX_INPUT_MIME_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/avif"]);
const MIN_KTX2_PSNR_DB = 38;
const MAX_POSITION_ERROR_METRES = 0.001;
const SIMPLIFICATION_ERROR_BUDGET_METRES = 0.0005;
const QUANTIZATION_ERROR_BUDGET_METRES = MAX_POSITION_ERROR_METRES - SIMPLIFICATION_ERROR_BUDGET_METRES;
const VISURA_SIMPLIFICATION_ERROR = 0.000005;

function ktxTextureRoles(document) {
	if ([...(document.extensionsUsed || []), ...(document.extensionsRequired || [])].includes("KHR_texture_basisu")) {
		throw new Error("E_KTX_ALREADY_COMPRESSED: Bereits KTX2-kodierte GLBs werden unverändert beibehalten.");
	}
	const roles = new Map();
	const encountered = new Set();
	const remember = (value, slot) => {
		if (!value || !Number.isInteger(value.index)) return;
		encountered.add(value.index);
		const role = SRGB_TEXTURE_SLOTS.has(slot) ? "srgb" : LINEAR_TEXTURE_SLOTS.has(slot) ? "linear" : null;
		if (!role) throw new Error(`E_KTX_UNSAFE_TEXTURE_SLOT: Textur-Slot ${slot || "unbekannt"} ist nicht farbraumsicher.`);
		const previous = roles.get(value.index);
		if (previous && previous !== role) {
			throw new Error("E_KTX_AMBIGUOUS_COLORSPACE: Eine Textur wird gleichzeitig als sRGB und linear verwendet.");
		}
		roles.set(value.index, role);
	};
	const scan = (value, slot = "") => {
		if (Array.isArray(value)) return value.forEach((item) => scan(item, slot));
		if (!value || typeof value !== "object") return;
		if (Number.isInteger(value.index) && /texture$/i.test(slot)) remember(value, slot);
		for (const [key, item] of Object.entries(value)) scan(item, key);
	};
	for (const material of document.materials || []) scan(material);
	return {roles, encountered};
}

function imageSourceIndex(texture) {
	const extensions = texture?.extensions;
	const alternativeSources = [];
	if (extensions && typeof extensions === "object") {
		for (const name of ["EXT_texture_webp", "EXT_texture_avif"]) {
			if (Number.isInteger(extensions[name]?.source)) alternativeSources.push(extensions[name].source);
		}
	}
	const distinctAlternatives = [...new Set(alternativeSources)];
	if (distinctAlternatives.length > 1) {
		throw new Error("E_KTX_AMBIGUOUS_TEXTURE_SOURCE: Mehrere unterschiedliche alternative Bildquellen können nicht sicher priorisiert werden.");
	}
	// EXT_texture_webp/avif are the representation selected by the viewer.
	// When present they must win over the optional core-image fallback.
	if (distinctAlternatives.length) return distinctAlternatives[0];
	return Number.isInteger(texture?.source) ? texture.source : null;
}

function requiresOnePixelKtxExpansion(width, height) {
	return width === 1 && height === 1;
}

async function ktxInputImage(image, mimeType, outputPath) {
	let input = image;
	let extension = mimeType === "image/jpeg" ? "jpg" : "png";
	if (mimeType === "image/webp" || mimeType === "image/avif") {
		input = await sharp(image, {limitInputPixels: false}).png().toBuffer();
		extension = "png";
	}
	if (!KTX_INPUT_MIME_TYPES.has(mimeType)) {
		throw new Error(`E_KTX_UNSUPPORTED_IMAGE: ${mimeType || "unbekannt"} kann nicht sicher nach KTX2 umgesetzt werden.`);
	}
	const metadata = await sharp(input, {limitInputPixels: false}).metadata();
	if (!metadata.width || !metadata.height) throw new Error("E_KTX_UNREADABLE_IMAGE: Texturabmessungen sind nicht lesbar.");
	if (requiresOnePixelKtxExpansion(metadata.width, metadata.height)) {
		// KTX-Software 4.4.2 needs a physical 4x4 input for this degenerate
		// BasisU case. Replicating one texel preserves all colour and alpha.
		input = await sharp(input, {limitInputPixels: false}).resize(4, 4, {fit: "fill", kernel: "lanczos3"}).png().toBuffer();
		extension = "png";
	}
	await writeFile(`${outputPath}.${extension}`, input);
	return {path: `${outputPath}.${extension}`, hasAlpha: metadata.hasAlpha === true};
}

async function createKtx2Candidate(source, destination) {
	const raw = await glbJson(source);
	const {roles, encountered} = ktxTextureRoles(raw);
	if (!roles.size) throw new Error("E_KTX_NO_ELIGIBLE_TEXTURES: GLB enthält keine sicher zuordenbare Rastertextur.");
	const textures = raw.textures || [];
	const images = raw.images || [];
	const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
	const document = await io.read(source);
	const documentTextures = document.getRoot().listTextures();
	// In glTF-Transform 4.4.2 the document owns textures per glTF *image*,
	// whereas raw glTF textures are source+sampler references. A valid asset
	// may therefore have several raw textures for one image (different samplers).
	if (documentTextures.length !== images.length) {
		throw new Error("E_KTX_TEXTURE_MAPPING: Bildabbildung konnte nicht eindeutig geladen werden.");
	}
	const imageRoles = new Map();
	for (const textureIndex of encountered) {
		const imageIndex = imageSourceIndex(textures[textureIndex]);
		if (!Number.isInteger(imageIndex) || !images[imageIndex]) {
			throw new Error("E_KTX_TEXTURE_SOURCE: Textur hat keine unterstützte eingebettete Bildquelle.");
		}
		const colorSpace = roles.get(textureIndex);
		const previousRole = imageRoles.get(imageIndex);
		if (previousRole && previousRole !== colorSpace) {
			throw new Error("E_KTX_AMBIGUOUS_COLORSPACE: Eine Bildquelle wird gleichzeitig als sRGB und linear verwendet.");
		}
		imageRoles.set(imageIndex, colorSpace);
	}
	const workDir = await mkdtemp(path.join(tmpdir(), "dronautix-ktx2-"));
	try {
		const basisu = document.createExtension(KHRTextureBasisu).setRequired(true);
		for (const [imageIndex, colorSpace] of imageRoles) {
			const documentTexture = documentTextures[imageIndex];
			const image = documentTexture?.getImage();
			const mimeType = documentTexture?.getMimeType();
			if (!image) throw new Error("E_KTX_TEXTURE_SOURCE: Texturbild konnte nicht gelesen werden.");
			const input = await ktxInputImage(image, mimeType, path.join(workDir, `image-${imageIndex}`));
			const output = path.join(workDir, `image-${imageIndex}.ktx2`);
			const format = `${input.hasAlpha ? "R8G8B8A8" : "R8G8B8"}_${colorSpace === "srgb" ? "SRGB" : "UNORM"}`;
			const encoding = colorSpace === "srgb"
				? ["--encode", "basis-lz", "--qlevel", "255", "--clevel", "6"]
				: ["--encode", "uastc", "--uastc-quality", "4", "--zstd", "18"];
			await executeKtxWithQualityAudit([
				// Full-resolution mipmaps remain GPU-compressed in the viewer.
				// Colour textures use high-quality ETC1S for transfer size; linear
				// data maps use high-quality UASTC to avoid block/RDO artefacts.
				"create", ...encoding, "--generate-mipmap", "--mipmap-filter", "lanczos4", "--compare-psnr",
				"--assign-tf", colorSpace === "srgb" ? "srgb" : "linear",
				"--assign-primaries", colorSpace === "srgb" ? "bt709" : "none",
				"--format", format, input.path, output,
			]);
			documentTexture.setImage(await readFile(output)).setMimeType("image/ktx2");
		}
		await io.write(destination, document);
		if (!basisu) throw new Error("E_KTX_EXTENSION: KHR_texture_basisu wurde nicht erzeugt.");
	} finally {
		await rm(workDir, {recursive: true, force: true});
	}
}

async function createLosslessMeshoptCandidate(source, destination) {
	const reordered = `${destination}.reordered.glb`;
	try {
		await execute(["reorder", source, reordered, "--target", "size"]);
		const {MeshoptEncoder} = await import("meshoptimizer");
		await MeshoptEncoder.ready;
		const io = new NodeIO()
			.registerExtensions(ALL_EXTENSIONS)
			.registerDependencies({"meshopt.encoder": MeshoptEncoder});
		const document = await io.read(reordered);
		document.createExtension(EXTMeshoptCompression)
			.setRequired(true)
			.setEncoderOptions({method: EXTMeshoptCompression.EncoderMethod.QUANTIZE});
		await io.write(destination, document);
	} finally {
		await rm(reordered, {force: true});
	}
}

function attributeTolerance(semantic) {
	if (semantic === "NORMAL" || semantic === "TANGENT") return 0.0001;
	if (semantic.startsWith("TEXCOORD_") || semantic.startsWith("COLOR_")) return 0.00002;
	if (semantic.startsWith("WEIGHTS_") || semantic.startsWith("_")) return 0.00002;
	return 0;
}

function transformPoint(point, matrix) {
	const x = point[0], y = point[1], z = point[2];
	const divisor = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15] || 1;
	return [
		(matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]) / divisor,
		(matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]) / divisor,
		(matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]) / divisor,
	];
}

function worldTransformNormUpperBound(matrix) {
	if (!matrix || matrix.length < 16) return NaN;
	const values = [matrix[0], matrix[1], matrix[2], matrix[4], matrix[5], matrix[6], matrix[8], matrix[9], matrix[10]];
	if (values.some((value) => !Number.isFinite(value))) return NaN;
	const columnNorm = Math.max(
		Math.abs(matrix[0]) + Math.abs(matrix[1]) + Math.abs(matrix[2]),
		Math.abs(matrix[4]) + Math.abs(matrix[5]) + Math.abs(matrix[6]),
		Math.abs(matrix[8]) + Math.abs(matrix[9]) + Math.abs(matrix[10]),
	);
	const rowNorm = Math.max(
		Math.abs(matrix[0]) + Math.abs(matrix[4]) + Math.abs(matrix[8]),
		Math.abs(matrix[1]) + Math.abs(matrix[5]) + Math.abs(matrix[9]),
		Math.abs(matrix[2]) + Math.abs(matrix[6]) + Math.abs(matrix[10]),
	);
	return Math.sqrt(columnNorm * rowNorm);
}

function accessorsMatch(source, output, tolerance) {
	if (!source || !output) return source === output;
	if (source.getCount() !== output.getCount() || source.getElementSize() !== output.getElementSize()) return false;
	const sourceValue = [];
	const outputValue = [];
	for (let index = 0; index < source.getCount(); index++) {
		source.getElement(index, sourceValue);
		output.getElement(index, outputValue);
		for (let component = 0; component < sourceValue.length; component++) {
			if (Math.abs(sourceValue[component] - outputValue[component]) > tolerance) return false;
		}
	}
	return true;
}

function assertQuantizationAudit(beforeDocument, afterDocument, maximumPositionErrorMetres) {
	const beforeRoot = beforeDocument.getRoot();
	const afterRoot = afterDocument.getRoot();
	const beforeMeshes = beforeRoot.listMeshes();
	const afterMeshes = afterRoot.listMeshes();
	const beforeNodes = beforeRoot.listNodes();
	const afterNodes = afterRoot.listNodes();
	if (beforeMeshes.length !== afterMeshes.length || beforeNodes.length !== afterNodes.length) {
		throw new Error("E_QUANTIZATION_STRUCTURE: Quantisierung hat Mesh- oder Node-Struktur verändert.");
	}
	for (let meshIndex = 0; meshIndex < beforeMeshes.length; meshIndex++) {
		const sourceMesh = beforeMeshes[meshIndex].listPrimitives();
		const outputMesh = afterMeshes[meshIndex].listPrimitives();
		if (sourceMesh.length !== outputMesh.length) throw new Error("E_QUANTIZATION_STRUCTURE: Primitive-Anzahl wurde verändert.");
		for (let primitiveIndex = 0; primitiveIndex < sourceMesh.length; primitiveIndex++) {
			const source = sourceMesh[primitiveIndex];
			const output = outputMesh[primitiveIndex];
			if (source.getMode() !== output.getMode() || source.listTargets().length !== output.listTargets().length
				|| !accessorsMatch(source.getIndices(), output.getIndices(), 0)) {
				throw new Error("E_QUANTIZATION_TOPOLOGY: Primitive-Topologie wurde verändert.");
			}
			const sourceSemantics = source.listSemantics().sort();
			const outputSemantics = output.listSemantics().sort();
			if (sourceSemantics.join("\0") !== outputSemantics.join("\0")) {
				throw new Error("E_QUANTIZATION_ATTRIBUTES: Vertex-Attribute wurden verändert.");
			}
			for (const semantic of sourceSemantics) {
				if (semantic === "POSITION") continue;
				const tolerance = attributeTolerance(semantic);
				if (!accessorsMatch(source.getAttribute(semantic), output.getAttribute(semantic), tolerance)) {
					throw new Error(`E_QUANTIZATION_ATTRIBUTE_ERROR: ${semantic} überschreitet die kontrollierte Fehlerschranke.`);
				}
			}
		}
	}
	const beforeMeshIndex = new Map(beforeMeshes.map((mesh, index) => [mesh, index]));
	const afterMeshIndex = new Map(afterMeshes.map((mesh, index) => [mesh, index]));
	for (let nodeIndex = 0; nodeIndex < beforeNodes.length; nodeIndex++) {
		const sourceNode = beforeNodes[nodeIndex];
		const outputNode = afterNodes[nodeIndex];
		const sourceMeshIndex = sourceNode.getMesh() ? beforeMeshIndex.get(sourceNode.getMesh()) : null;
		const outputMeshIndex = outputNode.getMesh() ? afterMeshIndex.get(outputNode.getMesh()) : null;
		if (sourceMeshIndex !== outputMeshIndex) {
			throw new Error("E_QUANTIZATION_STRUCTURE: Node-Mesh-Zuordnung wurde verändert.");
		}
		if (sourceMeshIndex === null) continue;
		const sourceMesh = beforeMeshes[sourceMeshIndex].listPrimitives();
		const outputMesh = afterMeshes[outputMeshIndex].listPrimitives();
		for (let primitiveIndex = 0; primitiveIndex < sourceMesh.length; primitiveIndex++) {
			const sourcePositions = sourceMesh[primitiveIndex].getAttribute("POSITION");
			const outputPositions = outputMesh[primitiveIndex].getAttribute("POSITION");
			if (!sourcePositions || !outputPositions || sourcePositions.getCount() !== outputPositions.getCount()) {
				throw new Error("E_QUANTIZATION_POSITION: POSITION-Accessor wurde verändert.");
			}
			const sourceValue = [];
			const outputValue = [];
			for (let index = 0; index < sourcePositions.getCount(); index++) {
				sourcePositions.getElement(index, sourceValue);
				outputPositions.getElement(index, outputValue);
				const sourcePoint = transformPoint(sourceValue, sourceNode.getWorldMatrix());
				const outputPoint = transformPoint(outputValue, outputNode.getWorldMatrix());
				const error = Math.hypot(
					sourcePoint[0] - outputPoint[0], sourcePoint[1] - outputPoint[1], sourcePoint[2] - outputPoint[2],
				);
				if (error > maximumPositionErrorMetres) {
					throw new Error(
						`E_QUANTIZATION_POSITION_ERROR: ${error.toFixed(6)} m überschreitet das verbleibende Fehlerbudget.`,
					);
				}
			}
		}
	}
}

async function simplificationNormalizedBudget(document) {
	const root = document.getRoot();
	const meshes = root.listMeshes();
	const primitives = meshes.flatMap((mesh) => mesh.listPrimitives());
	if (!primitives.length || primitives.some((primitive) => primitive.getMode() !== 4
		|| !primitive.getIndices() || !(primitive.getAttribute("POSITION")?.getArray() instanceof Float32Array))) {
		return null;
	}
	const {MeshoptSimplifier} = await import("meshoptimizer");
	await MeshoptSimplifier.ready;
	const worldAmplification = new Map(meshes.map((mesh) => [mesh, 1]));
	for (const node of root.listNodes()) {
		const mesh = node.getMesh();
		if (!mesh) continue;
		const amplification = worldTransformNormUpperBound(node.getWorldMatrix());
		if (!(amplification > 0) || !Number.isFinite(amplification)) return null;
		worldAmplification.set(mesh, Math.max(worldAmplification.get(mesh) || 1, amplification));
	}
	const scales = meshes.flatMap((mesh) => mesh.listPrimitives().map((primitive) => (
		MeshoptSimplifier.getScale(primitive.getAttribute("POSITION").getArray(), 3)
		* (worldAmplification.get(mesh) || 1)
	)));
	if (scales.some((scale) => !(scale > 0) || !Number.isFinite(scale))) return null;
	return Math.min(VISURA_SIMPLIFICATION_ERROR, SIMPLIFICATION_ERROR_BUDGET_METRES / Math.max(...scales));
}

async function createVisuraSafeCandidate(source, destination) {
	const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
	const textureOnly = `${destination}.ktx2.glb`;
	const simplified = `${destination}.simplified.glb`;
	const quantized = `${destination}.quantized.glb`;
	try {
		await createKtx2Candidate(source, textureOnly);
		const document = await io.read(textureOnly);
		const normalizedSimplificationBudget = await simplificationNormalizedBudget(document);
		let geometrySource = textureOnly;
		let geometryDocument = document;
		if (normalizedSimplificationBudget !== null) {
			await execute([
				"simplify", textureOnly, simplified,
				"--ratio", "0", "--error", normalizedSimplificationBudget.toPrecision(12),
			]);
			geometrySource = simplified;
			geometryDocument = await io.read(simplified);
		}
		let compressionSource = geometrySource;
		try {
			await execute([
				"quantize", geometrySource, quantized,
				"--quantization-volume", "mesh",
				"--quantize-position", "16", "--quantize-normal", "16",
				"--quantize-texcoord", "16", "--quantize-color", "16",
				"--quantize-weight", "16", "--quantize-generic", "16",
			]);
			const quantizedDocument = await io.read(quantized);
			assertQuantizationAudit(
				geometryDocument,
				quantizedDocument,
				QUANTIZATION_ERROR_BUDGET_METRES,
			);
			compressionSource = quantized;
		} catch (error) {
			process.stderr.write(`Visura-Kandidat: Geometrie bleibt wegen 1-mm-Vertrag unquantisiert (${error?.message || error}).\n`);
		}
		await createLosslessMeshoptCandidate(compressionSource, destination);
	} finally {
		await rm(textureOnly, {force: true});
		await rm(simplified, {force: true});
		await rm(quantized, {force: true});
	}
}

function assertKtxPolicyMatrix() {
	const document = {materials: [{
		pbrMetallicRoughness: {
			baseColorTexture: {index: 0, extensions: {KHR_texture_transform: {offset: [0.25, 0.5], scale: [0.5, 0.5]}}},
			metallicRoughnessTexture: {index: 1},
		},
		normalTexture: {index: 2}, occlusionTexture: {index: 3}, emissiveTexture: {index: 4},
		extensions: {
			KHR_materials_clearcoat: {clearcoatTexture: {index: 5}, clearcoatNormalTexture: {index: 6}},
			KHR_materials_sheen: {sheenColorTexture: {index: 7}, sheenRoughnessTexture: {index: 8}},
			KHR_materials_specular: {specularColorTexture: {index: 9}, specularTexture: {index: 10}},
		},
	}]};
	const {roles} = ktxTextureRoles(document);
	const expected = ["srgb", "linear", "linear", "linear", "srgb", "linear", "linear", "srgb", "linear", "srgb", "linear"];
	if (expected.some((role, index) => roles.get(index) !== role)) {
		throw new Error("KTX2-Selbsttest: Textur-Slot-Farbräume sind nicht deterministisch.");
	}
	for (const mimeType of KTX_INPUT_MIME_TYPES) {
		if (!KTX_INPUT_MIME_TYPES.has(mimeType)) throw new Error("KTX2-Selbsttest: MIME-Matrix ist ungültig.");
	}
	for (const [width, height] of [[3, 5], [7, 9], [1023, 513]]) {
		if (requiresOnePixelKtxExpansion(width, height)) {
			throw new Error("KTX2-Selbsttest: Nicht-4er-Abmessung wurde fälschlich resampelt.");
		}
	}
	if (imageSourceIndex({source: 9, extensions: {EXT_texture_webp: {source: 4}}}) !== 4
		|| imageSourceIndex({source: 9, extensions: {EXT_texture_avif: {source: 5}}}) !== 5) {
		throw new Error("KTX2-Selbsttest: WebP/AVIF-Quellen werden nicht aufgelöst.");
	}
	let ambiguousSource = false;
	try {
		imageSourceIndex({extensions: {EXT_texture_webp: {source: 4}, EXT_texture_avif: {source: 5}}});
	} catch (error) {
		ambiguousSource = String(error?.message || error).includes("E_KTX_AMBIGUOUS_TEXTURE_SOURCE");
	}
	if (!ambiguousSource) throw new Error("KTX2-Selbsttest: mehrdeutige alternative Bildquellen wurden nicht gesperrt.");
	let ambiguous = false;
	try {
		ktxTextureRoles({materials: [{pbrMetallicRoughness: {baseColorTexture: {index: 0}}, normalTexture: {index: 0}}]});
	} catch (error) {
		ambiguous = String(error?.message || error).includes("E_KTX_AMBIGUOUS_COLORSPACE");
	}
	if (!ambiguous) throw new Error("KTX2-Selbsttest: gemischter Farbraum wurde nicht gesperrt.");
	let existingKtx = false;
	try {
		ktxTextureRoles({extensionsUsed: ["KHR_texture_basisu"]});
	} catch (error) {
		existingKtx = String(error?.message || error).includes("E_KTX_ALREADY_COMPRESSED");
	}
	if (!existingKtx) throw new Error("KTX2-Selbsttest: bestehendes KTX2 wurde nicht konservativ übersprungen.");
}

function assertWorldTransformBudgetMatrix() {
	const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
	const scaled = [1000, 0, 0, 0, 0, 1000, 0, 0, 0, 0, 1000, 0, 0, 0, 0, 1];
	const sheared = [1, 0, 0, 0, 10, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
	if (worldTransformNormUpperBound(identity) !== 1
		|| worldTransformNormUpperBound(scaled) !== 1000
		|| worldTransformNormUpperBound(sheared) < Math.hypot(10, 1)) {
		throw new Error("GLB-Selbsttest: Welttransformation wird für die metrische Fehlerschranke nicht konservativ erfasst.");
	}
}

async function main() {
	if (process.argv[2] === "--self-test") {
		await access(cli);
		await access(ktx);
		await execute(["--version"]);
		await executeKtx(["--version"]);
		assertKtxPolicyMatrix();
		assertWorldTransformBudgetMatrix();
		process.stdout.write("gltf-transform and KTX BasisU encoder ready\n");
		return;
	}
	const [, , codec, source, destination, ...extra] = process.argv;
	if (extra.length || !source || !destination || !["conservative", "visura-safe", "meshopt", "draco", "ktx2"].includes(codec)) {
		throw new Error("Aufruf: optimize-glb.mjs <conservative|visura-safe|meshopt|draco|ktx2> <eingabe.glb> <ausgabe.glb>");
	}
	if (codec === "conservative") {
		const temporary = `${destination}.dedup.glb`;
		const pruned = `${destination}.prune.glb`;
		await execute(["dedup", source, temporary]);
		await execute(["prune", temporary, pruned]);
		await execute(["reorder", pruned, destination, "--target", "size"]);
		return;
	}
	if (!(await isStaticScene(source))) {
		process.stderr.write("E_NOT_STATIC: Starke GLB-Optimierung ist für Animationen, Skins oder Morph Targets gesperrt.\n");
		process.exitCode = 20;
		return;
	}
	if (codec === "visura-safe") {
		await createVisuraSafeCandidate(source, destination);
		return;
	}
	if (codec === "ktx2") {
		await createKtx2Candidate(source, destination);
		return;
	}
	if (codec === "meshopt") {
		await execute([
			"meshopt", source, destination, "--level", "medium",
			"--quantize-position", "16", "--quantize-normal", "16",
			"--quantize-texcoord", "16", "--quantize-color", "16",
			"--quantize-weight", "16", "--quantize-generic", "16",
		]);
		return;
	}
	await execute([
		"draco", source, destination,
		"--encode-speed", "5", "--decode-speed", "5",
		"--quantize-position", "20", "--quantize-normal", "16",
		"--quantize-texcoord", "16", "--quantize-color", "16", "--quantize-generic", "16",
	]);
}

main().catch((error) => fail(error));
