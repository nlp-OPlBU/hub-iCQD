import { ResearchDetail } from "@/components/ResearchDetail";

export default async function ResearchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ResearchDetail id={id} />;
}
