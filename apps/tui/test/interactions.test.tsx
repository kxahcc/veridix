import { afterEach, describe, expect, it } from "vitest";
import { render } from "ink-testing-library";
import { App } from "../src/index.js";


const instances: Array<{ unmount: () => void }> = [];

afterEach(() => {
  for (const instance of instances.splice(0)) {
    instance.unmount();
  }
});


function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}


describe("TUI interactions", () => {
  it("renders the real shell surface", async () => {
    process.env.VERIDIX_CONTROL_URL = "http://127.0.0.1:1";
    const instance = render(<App />);
    instances.push(instance);
    await sleep(180);
    instance.stdin.write("\r");
    await sleep(100);

    const frame = instance.lastFrame() ?? "";
    expect(frame).toContain("VERIDIX AGENT");
    expect(frame).toContain("运行列表");
    expect(frame).toContain("/ 打开命令");
  });
});
