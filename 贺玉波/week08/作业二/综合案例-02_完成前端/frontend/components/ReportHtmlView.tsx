export function ReportHtmlView({ html }: { html: string }) {
  return (
    <iframe
      title="HTML 报告"
      srcDoc={html}
      sandbox=""
      className="h-[70vh] w-full rounded-lg border border-zinc-200 bg-white"
    />
  );
}
