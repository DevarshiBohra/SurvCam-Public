import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const gradle = process.platform === "win32" ? "gradle.bat" : "gradle";
const result = spawnSync(gradle, ["--no-daemon", "assembleRelease"], { stdio: "inherit", shell: true });

if (result.status === 0) {
  const apk = "app/build/outputs/apk/release/app-release-unsigned.apk";
  console.log(existsSync(apk) ? `Unsigned APK ready: ${apk}` : "Build finished, but APK path was not found.");
}

process.exit(result.status ?? 1);
