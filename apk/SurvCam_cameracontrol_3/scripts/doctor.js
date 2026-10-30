import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

function runVersionCheck(command) {
  execFileSync(command, { stdio: "pipe", shell: true });
}

const checks = [
  ["node --version", "node"],
  ["java -version", "java"],
  ["gradle --version", "gradle"]
];

for (const [command, label] of checks) {
  try {
    runVersionCheck(command);
    console.log(`OK: ${label}`);
  } catch {
    console.log(`MISSING: ${label}`);
  }
}

const sdk = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT;
if (!sdk) {
  console.log("MISSING: ANDROID_HOME or ANDROID_SDK_ROOT");
} else if (!existsSync(join(sdk, "platform-tools"))) {
  console.log(`CHECK: Android SDK found at ${sdk}, but platform-tools was not found`);
} else {
  console.log(`OK: Android SDK at ${sdk}`);
}
