import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("测试基建", () => {
  it("jsdom + RTL 可用", () => {
    render(<div>基建就绪</div>);
    expect(screen.getByText("基建就绪")).toBeInTheDocument();
  });
});
