import type { Conclusion, ReportContent } from "@/lib/types";

function ConclusionItem({ c }: { c: Conclusion }) {
  return (
    <li className="flex flex-col gap-1 text-sm text-zinc-700">
      <span>{c.text}</span>
      <span className="flex flex-wrap items-center gap-2">
        {c.is_model_inference && (
          <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-700">模型推断</span>
        )}
        {c.sources.map((s) => {
          const isSafe = /^https?:\/\//i.test(s.url);
          return isSafe ? (
            <a
              key={s.url}
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-600 hover:underline"
            >
              {s.title || s.url}
            </a>
          ) : (
            <span key={s.url} className="text-xs text-zinc-500">
              {s.title || s.url}
            </span>
          );
        })}
      </span>
    </li>
  );
}

export function ReportView({ report }: { report: ReportContent }) {
  return (
    <article className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-bold text-zinc-900">{report.title}</h1>
        <p className="text-sm leading-relaxed text-zinc-600">{report.summary}</p>
      </header>

      {report.sections.map((section) => (
        <section key={section.heading} className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold text-zinc-900">{section.heading}</h2>
          {section.body.split("\n").filter(Boolean).map((para, i) => (
            <p key={i} className="text-sm leading-7 text-zinc-700">
              {para}
            </p>
          ))}
          {section.conclusions.length > 0 && (
            <ul className="flex flex-col gap-2 rounded-lg bg-zinc-50 p-3">
              {section.conclusions.map((c, i) => (
                <ConclusionItem key={i} c={c} />
              ))}
            </ul>
          )}
        </section>
      ))}

      {report.key_conclusions.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold text-zinc-900">关键结论</h2>
          <ul className="flex flex-col gap-2">
            {report.key_conclusions.map((c, i) => (
              <ConclusionItem key={i} c={c} />
            ))}
          </ul>
        </section>
      )}

      {report.open_questions.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-lg font-semibold text-zinc-900">遗留问题</h2>
          <ul className="flex list-inside list-disc flex-col gap-1">
            {report.open_questions.map((q, i) => (
              <li key={i} className="text-sm text-zinc-700">
                {q}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
