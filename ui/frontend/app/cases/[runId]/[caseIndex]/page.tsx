import { fetchCase } from "@/lib/api";
import { CaseDetailView } from "@/components/case-detail";

interface PageProps {
  params: Promise<{ runId: string; caseIndex: string }>;
}

export default async function CaseDetailPage({ params }: PageProps) {
  const { runId, caseIndex } = await params;
  const caseData = await fetchCase(runId, Number(caseIndex));

  return <CaseDetailView caseData={caseData} />;
}
