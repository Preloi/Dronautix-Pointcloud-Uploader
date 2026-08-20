import { ILogger, NodeIO, Transform, Verbosity, vec2 } from "@gltf-transform/core";
import { TextureResizeFilter } from "@gltf-transform/functions";
import { Logger, ParsedOption, Validator as Validator$1 } from "@donmccurdy/caporal";
import { ChildProcess, spawn as spawn$1 } from "node:child_process";

//#region src/program.d.ts
/**********************************************************************************************
 * Program.
 */
interface IProgram {
  command: (name: string, desc: string) => ICommand;
  option: (name: string, desc: string, options: IProgramOptions) => this;
  section: (name: string, icon: string) => this;
}
interface IExecOptions {
  silent?: boolean;
}
interface IInternalProgram extends IProgram {
  version: (version: string) => this;
  description: (desc: string) => this;
  disableGlobalOption: (name: string) => this;
  run: () => this;
  exec: (args: unknown[], options?: IExecOptions) => Promise<void>;
}
interface IProgramOptions<T = unknown> {
  default?: T;
  validator?: Validator$1;
  action?: IActionFn;
  hidden?: boolean;
}
type IActionFn = (params: {
  args: Record<string, unknown>;
  options: Record<string, unknown>;
  logger: Logger$1;
}) => void;
interface IHelpOptions {
  sectionName?: string;
}
declare class ProgramImpl implements IInternalProgram {
  version(version: string): this;
  description(desc: string): this;
  help(help: string, options?: IHelpOptions): this;
  section(_name: string, _icon: string): this;
  command(name: string, desc: string): ICommand;
  option<T>(name: string, desc: string, options: IProgramOptions<T>): this;
  disableGlobalOption(name: string): this;
  run(): this;
  exec(args: unknown[], options?: IExecOptions): Promise<void>;
}
/**********************************************************************************************
 * Command.
 */
interface ICommand {
  help: (text: string) => this;
  argument: (name: string, desc: string) => this;
  option: (name: string, desc: string, options?: ICommandOptions) => this;
  action: (fn: IActionFn) => this;
  alias: (name: string) => this;
}
interface ICommandOptions {
  required?: boolean;
  default?: ParsedOption;
  validator?: Validator$1;
  hidden?: boolean;
}
declare const program: ProgramImpl;
/**********************************************************************************************
 * Validator.
 */
declare const Validator: Record<'NUMBER' | 'ARRAY' | 'BOOLEAN' | 'STRING', Validator$1>;
/**********************************************************************************************
 * Logger.
 */
declare class Logger$1 implements ILogger {
  _logger: Logger;
  _verbosity: Verbosity;
  constructor(logger: Logger);
  getVerbosity(): Verbosity;
  setVerbosity(verbosity: Verbosity): void;
  debug(msg: string): void;
  info(msg: string): void;
  warn(msg: string): void;
  error(msg: string): void;
}
//#endregion
//#region src/transforms/ktxdecompress.d.ts
interface KTXDecompressOptions {
  jobs?: number;
  /**
   * Whether to clean up temporary files created during texture compression. See
   * verbose log output for temporary file paths. Default: true.
   */
  cleanup?: boolean;
}
declare const ktxdecompress: (options?: KTXDecompressOptions) => Transform;
//#endregion
//#region src/transforms/ktxfix.d.ts
declare function ktxfix(): Transform;
//#endregion
//#region src/transforms/merge.d.ts
interface MergeOptions {
  io: NodeIO;
  paths: string[];
  partition?: boolean;
  mergeScenes?: boolean;
}
declare const merge: (options: MergeOptions) => Transform;
//#endregion
//#region src/transforms/toktx.d.ts
/**********************************************************************************************
 * Interfaces.
 */
declare const Mode: {
  ETC1S: string;
  UASTC: string;
};
declare const Filter: {
  BOX: string;
  TENT: string;
  BELL: string;
  BSPLINE: string;
  MITCHELL: string;
  LANCZOS3: string;
  LANCZOS4: string;
  LANCZOS6: string;
  LANCZOS12: string;
  BLACKMAN: string;
  KAISER: string;
  GAUSSIAN: string;
  CATMULLROM: string;
  QUADRATIC_INTERP: string;
  QUADRATIC_APPROX: string;
  QUADRATIC_MIX: string;
};
interface GlobalOptions {
  /** Instance of the Sharp encoder, required if resizing textures. */
  encoder: unknown;
  mode: string;
  /** Pattern identifying textures to compress, matched to name or URI. */
  pattern?: RegExp | null;
  /**
   * Pattern matching the material texture slot(s) to be compressed or converted.
   * Passing a string (glob) is deprecated; use a RegExp instead.
   */
  slots?: RegExp | null;
  /** Interpolation used for generating mipmaps. Default: 'lanczos4'. */
  filter?: string;
  filterScale?: number;
  resize?: vec2 | 'nearest-pot' | 'ceil-pot' | 'floor-pot';
  /** Interpolation used if resizing. Default: TextureResizeFilter.LANCZOS3. */
  resizeFilter?: TextureResizeFilter;
  jobs?: number;
  /**
   * Whether to clean up temporary files created during texture compression. See
   * verbose log output for temporary file paths. Default: true.
   */
  cleanup?: boolean;
  /**
   * Attempts to avoid processing images that could exceed memory or other other
   * limits, throwing an error instead. Default: true.
   * @experimental
   */
  limitInputPixels?: boolean;
  /** Whether to generate mipmaps. Default: true. */
  mipmaps?: boolean;
}
interface ETC1SOptions extends GlobalOptions {
  quality?: number;
  compression?: number;
  maxEndpoints?: number;
  maxSelectors?: number;
  rdo?: boolean;
  rdoThreshold?: number;
}
interface UASTCOptions extends GlobalOptions {
  level?: number;
  rdo?: boolean;
  rdoLambda?: number;
  rdoDictionarySize?: number;
  rdoBlockScale?: number;
  rdoStdDev?: number;
  rdoMultithreading?: boolean;
  zstd?: number;
}
declare const ETC1S_DEFAULTS: Omit<ETC1SOptions, 'encoder' | 'mode'>;
declare const UASTC_DEFAULTS: Omit<UASTCOptions, 'encoder' | 'mode'>;
/**********************************************************************************************
 * Implementation.
 */
declare const toktx: (options: ETC1SOptions | UASTCOptions) => Transform;
declare function checkKTXSoftware(logger: ILogger): Promise<string>;
//#endregion
//#region src/transforms/xmp.d.ts
interface XMPOptions {
  packet?: string;
  reset?: boolean;
}
declare const XMP_DEFAULTS: {
  packet: string;
  reset: boolean;
};
declare const xmp: (_options?: XMPOptions) => Transform;
//#endregion
//#region src/utils/format.d.ts
declare function formatLong(x: number): string;
declare function formatBytes(bytes: number, decimals?: number): string;
declare function formatParagraph(str: string): string;
declare function formatHeader(title: string): string;
declare enum TableFormat {
  PRETTY = "pretty",
  CSV = "csv",
  MD = "md"
}
declare function formatTable(format: TableFormat, head: string[], rows: string[][]): Promise<string>;
declare function formatXMP(value: string | number | boolean | Record<string, unknown> | null): string;
//#endregion
//#region src/utils/log.d.ts
declare let log: typeof console.log;
declare function mockConsoleLog(_log: (...data: unknown[]) => void): void;
//#endregion
//#region src/utils/p-limit.d.ts
type PLimitFn<T> = ((value: T, index: number) => Promise<void>) | ((value: T, index: number) => void);
/** Runs multiple async functions, with limited concurrency. */
declare function pLimit<T>(items: T[], limit: number, fn: PLimitFn<T>): Promise<void>;
//#endregion
//#region src/utils/process.d.ts
declare let spawn: typeof spawn$1;
declare let commandExists: typeof _commandExists;
declare let waitExit: typeof _waitExit;
declare function mockSpawn(_spawn: unknown): void;
declare function mockCommandExists(fn: (n: TrustedCommand) => Promise<boolean>): void;
declare function mockWaitExit(_waitExit: (process: ChildProcess) => Promise<[unknown, string, string]>): void;
declare enum TrustedCommand {
  KTX = "ktx"
}
/**
 * Resolves 'true' if an executable command-line command with the given name
 * exists, otherwise returns false. This is a stripped-down version of the
 * npm package, `command-exists` (https://github.com/mathisonian/command-exists).
 */
declare function _commandExists(cmd: TrustedCommand): Promise<boolean>;
declare function _waitExit(process: ChildProcess): Promise<[unknown, string, string]>;
//#endregion
//#region src/cli.d.ts
declare const programReady: Promise<void>;
//#endregion
export { ETC1SOptions, ETC1S_DEFAULTS, Filter, MergeOptions, Mode, TableFormat, TrustedCommand, UASTCOptions, UASTC_DEFAULTS, Validator, XMPOptions, XMP_DEFAULTS, _waitExit, checkKTXSoftware, commandExists, formatBytes, formatHeader, formatLong, formatParagraph, formatTable, formatXMP, ktxdecompress, ktxfix, log, merge, mockCommandExists, mockConsoleLog, mockSpawn, mockWaitExit, pLimit, program, programReady, spawn, toktx, waitExit, xmp };