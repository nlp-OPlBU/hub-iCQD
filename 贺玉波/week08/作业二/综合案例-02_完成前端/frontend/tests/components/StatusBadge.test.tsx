import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "@/components/StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["pending", "排队中"],
    ["running", "研究中"],
    ["completed", "已完成"],
    ["failed", "失败"],
  ] as const)("status=%s 渲染「%s」", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toHaveAttribute("data-status", status);
  });
});
