import { Fragment, ReactNode } from "react";

/** 只认三种内联标记：**加粗**、`代码`、![图](/static/xxx)。别的原样输出，不做 HTML 解析。 */
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|!\[[^\]]*\]\([^)]+\))/g;

function inline(text: string): ReactNode[] {
  return text
    .split(INLINE)
    .filter((part) => part !== "")
    .map((part, index) => {
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        return <strong key={index}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        return <code key={index}>{part.slice(1, -1)}</code>;
      }
      const image = part.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
      if (image) {
        // 只渲染本站图片，避免模型编出外链被当成资源加载
        return image[2].startsWith("/static/") ? (
          <img key={index} className="md-img" src={image[2]} alt={image[1]} loading="lazy" />
        ) : null;
      }
      return <Fragment key={index}>{part}</Fragment>;
    });
}

/**
 * 极简 Markdown 渲染：标题 / 列表 / 段落 / 图片。
 * 不做完整解析，够 RAG 答案展示用，也避免引一个几百 KB 的依赖。
 */
export default function Markdown({ text }: { text: string }) {
  const cleaned = text
    .replace(/!\[[^\]]*\]\(doc:[^)]*\)/g, "")
    .replace(/<!--\s*page[^>]*-->/gi, "");
  const lines = cleaned.split(/\r?\n/);

  const blocks: ReactNode[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let key = 0;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push(<p key={`p${key++}`}>{inline(paragraph.join(" "))}</p>);
    paragraph = [];
  };

  const flushList = () => {
    if (!list || list.items.length === 0) {
      list = null;
      return;
    }
    const items = list.items.map((item, index) => <li key={index}>{inline(item)}</li>);
    blocks.push(list.ordered ? <ol key={`l${key++}`}>{items}</ol> : <ul key={`l${key++}`}>{items}</ul>);
    list = null;
  };

  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushAll();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushAll();
      const level = Math.min(heading[1].length + 2, 4);
      const Tag = (`h${level}` as unknown) as "h3";
      blocks.push(<Tag key={`h${key++}`}>{inline(heading[2])}</Tag>);
      continue;
    }
    const bullet = line.match(/^[-*·]\s+(.+)$/);
    const ordered = line.match(/^\d+[.、)]\s+(.+)$/);
    if (bullet || ordered) {
      flushParagraph();
      const wantOrdered = Boolean(ordered);
      if (!list || list.ordered !== wantOrdered) {
        flushList();
        list = { ordered: wantOrdered, items: [] };
      }
      list.items.push((bullet?.[1] ?? ordered?.[1]) as string);
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushAll();

  return <div className="markdown">{blocks}</div>;
}
