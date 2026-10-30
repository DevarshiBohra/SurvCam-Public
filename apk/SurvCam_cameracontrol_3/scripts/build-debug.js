import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const gradle = process.platform === "win32" ? "gradle.bat" : "gradle";
const result = spawnSync(gradle, ["--no-daemon", "assembleDebug"], { stdio: "inherit", shell: true });

if (result.status === 0) {
  const apk = "app/build/outputs/apk/debug/app-debug.apk";
  console.log(existsSync(apk) ? `APK ready: ${apk}` : "Build finished, but APK path was not found.");
}

process.exit(result.status ?? 1);
