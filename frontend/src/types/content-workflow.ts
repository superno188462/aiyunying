/** One reviewed content-production task returned by the FastAPI contract. */
export type ContentWorkflow = {
  id: number;
  topic: string;
  account_id: number | null;
  source_mode: "manual" | "account";
  stage: "created" | "researched" | "topics_ready" | "topic_selected" | "draft_ready" | "draft_approved" | "cards_ready";
  version: number;
  references: Array<{ note_id: string; note_url: string; title: string; content: string; likes: number | null; collects: number | null; comments: number | null; timestamp: number }>;
  candidates: Array<{ title: string; audience: string; angle: string; difference: string; evidence: string }>;
  selected_topic: { title?: string; audience?: string; angle?: string; difference?: string; evidence?: string };
  draft_title: string;
  draft_body: string;
  draft_versions: Array<{ title: string; body: string; source: "ai" | "human" }>;
  cards: Array<{ headline: string; body: string; visual_prompt?: string; asset_batch?: string }>;
  xhs_title: string;
  xhs_caption: string;
  created_at: string;
  updated_at: string;
};
