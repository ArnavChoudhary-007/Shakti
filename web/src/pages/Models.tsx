import { ModelPicker } from "@/components/ModelPicker";
import { Card, PageHeader } from "@/components/ui";

export default function ModelsPage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <PageHeader title="Models" description="Choose which AI model answers your questions, download new ones, or free up disk space." />
      <Card className="p-5">
        <ModelPicker />
      </Card>
    </div>
  );
}
