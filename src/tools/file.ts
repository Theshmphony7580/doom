import * as path from "node:path";
import * as fs from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import {
  createReadTool as createPiReadTool,
  createWriteTool as createPiWriteTool,
} from "@earendil-works/pi-coding-agent";
import type { HarnessTool } from "../types.ts";

export interface FileToolsOptions {
  cwd?: string;
  artifactsDir?: string;
}

/**
 * Resolves a requested file path so write operations target the root .artifacts/ directory.
 * Strips redundant leading ".artifacts/" and resolves relative to artifactsDir.
 */
export function resolveArtifactWritePath(
  inputPath: string,
  cwd: string = process.cwd(),
  artifactsDir: string = path.resolve(cwd, ".artifacts")
): { targetPath: string; displayPath: string } {
  const normInput = (inputPath || "").replace(/\\/g, "/").trim();
  const normCwd = cwd.replace(/\\/g, "/");
  const normArtifacts = artifactsDir.replace(/\\/g, "/");

  let subPath = normInput;

  // Handle absolute path input
  if (path.isAbsolute(inputPath)) {
    if (normInput.startsWith(normArtifacts)) {
      subPath = normInput.slice(normArtifacts.length);
    } else if (normInput.startsWith(normCwd)) {
      subPath = normInput.slice(normCwd.length);
    } else {
      subPath = path.basename(inputPath);
    }
  }

  // Normalize leading slashes or dots
  subPath = subPath.replace(/^[/\\]+/, "").replace(/^\.[/\\]+/, "");

  // Strip leading .artifacts folder if caller provided it
  if (subPath.startsWith(".artifacts/")) {
    subPath = subPath.slice(".artifacts/".length);
  } else if (subPath === ".artifacts") {
    subPath = "output.txt";
  }

  subPath = subPath.replace(/^[/\\]+/, "");
  if (!subPath) {
    subPath = "output.txt";
  }

  // Prevent directory traversal outside artifactsDir
  const resolvedTarget = path.resolve(artifactsDir, subPath);
  const safeTarget = resolvedTarget.startsWith(artifactsDir)
    ? resolvedTarget
    : path.resolve(artifactsDir, path.basename(subPath));

  const relFromCwd = path.relative(cwd, safeTarget).replace(/\\/g, "/");
  return {
    targetPath: safeTarget,
    displayPath: relFromCwd,
  };
}

async function fileExists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath, fsConstants.F_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Creates the standard file reading tool named "read".
 * Supports resolving paths from both cwd and the artifacts directory.
 */
export function createReadTool(
  cwd: string = process.cwd(),
  artifactsDir?: string
): HarnessTool {
  const baseTool = createPiReadTool(cwd);
  if (!artifactsDir) {
    return baseTool;
  }

  return {
    ...baseTool,
    description:
      "Read the contents of a file at the given path (resolves from root and .artifacts/). Supports optional line offset and line limit parameters.",
    execute: async (toolCallId, params, signal, onUpdate, ctx) => {
      const rawPath = typeof params?.path === "string" ? params.path : "";
      const cwdTarget = path.resolve(cwd, rawPath);
      const artifactTarget = path.resolve(artifactsDir, rawPath.replace(/^\.artifacts[/\\]?/, ""));

      // Check if file exists in cwd or in artifactsDir
      let targetPath = rawPath;
      if (await fileExists(cwdTarget)) {
        targetPath = cwdTarget;
      } else if (await fileExists(artifactTarget)) {
        targetPath = artifactTarget;
      }

      return baseTool.execute(
        toolCallId,
        { ...params, path: targetPath },
        signal,
        onUpdate,
        { ...ctx, cwd }
      );
    },
  };
}

/**
 * Creates the standard file writing tool named "write".
 * If artifactsDir is provided, all written files are routed into artifactsDir.
 */
export function createWriteTool(
  cwd: string = process.cwd(),
  artifactsDir?: string
): HarnessTool {
  const baseTool = createPiWriteTool(cwd);
  if (!artifactsDir) {
    return baseTool;
  }

  return {
    ...baseTool,
    description:
      "Write content to a file. All outputs are automatically saved and organized under the root .artifacts/ directory. Creates the file if it doesn't exist, overwrites if it does. Automatically creates parent directories.",
    promptSnippet: "Create or overwrite files under the root .artifacts/ directory",
    promptGuidelines: [
      "All file write operations are automatically routed and saved under the root .artifacts/ directory.",
      "Use write to create new files or completely rewrite existing files.",
    ],
    execute: async (toolCallId, params, signal, onUpdate, ctx) => {
      const rawPath = typeof params?.path === "string" ? params.path : "output.txt";
      const { targetPath, displayPath } = resolveArtifactWritePath(rawPath, cwd, artifactsDir);

      // Ensure target directory exists
      await fs.mkdir(path.dirname(targetPath), { recursive: true });

      // Execute write using the routed target path
      const result = await baseTool.execute(
        toolCallId,
        { ...params, path: targetPath },
        signal,
        onUpdate,
        { ...ctx, cwd }
      );

      // Format response text to show the clean routed path
      if (!result.isError && Array.isArray(result.content)) {
        return {
          ...result,
          content: [
            {
              type: "text",
              text: `Successfully wrote to ${displayPath}`,
            },
          ],
        };
      }

      return result;
    },
  };
}

/**
 * Creates a file reading tool aliased as "read_file"
 */
export function createFileReadTool(
  cwd: string = process.cwd(),
  artifactsDir?: string
): HarnessTool {
  const tool = createReadTool(cwd, artifactsDir);
  return {
    ...tool,
    name: "read_file",
    label: "read_file",
    description:
      "Read the contents of a file at the given path (resolves from root and .artifacts/). Supports optional line offset and line limit parameters.",
  };
}

/**
 * Creates a file writing tool aliased as "write_file".
 * If artifactsDir is provided, all written files are routed into artifactsDir.
 */
export function createFileWriteTool(
  cwd: string = process.cwd(),
  artifactsDir?: string
): HarnessTool {
  const tool = createWriteTool(cwd, artifactsDir);
  return {
    ...tool,
    name: "write_file",
    label: "write_file",
    description:
      "Write content to a file at the given path. All outputs are automatically routed and saved under the root .artifacts/ directory. Automatically creates parent directories if they do not exist.",
  };
}

/**
 * Returns all file system tools (read, write, read_file, write_file)
 * to ensure model compatibility regardless of tool naming convention.
 * Automatically routes write operations to the root .artifacts/ directory by default.
 */
export function createFileTools(options?: FileToolsOptions): HarnessTool[] {
  const workingDir = options?.cwd || process.cwd();
  const artifactsDir = options?.artifactsDir ?? path.resolve(workingDir, ".artifacts");

  return [
    createReadTool(workingDir, artifactsDir),
    createWriteTool(workingDir, artifactsDir),
    createFileReadTool(workingDir, artifactsDir),
    createFileWriteTool(workingDir, artifactsDir),
  ];
}

