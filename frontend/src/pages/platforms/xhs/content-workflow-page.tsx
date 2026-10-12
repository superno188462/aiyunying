import { Alert, Button, Card, Col, Empty, Input, InputNumber, List, message, Row, Segmented, Select, Space, Spin, Steps, Tag, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import { PageHeader } from "../../../components/layout/app-shell";
import { contentWorkflowApi, fetchAccounts } from "../../../lib/api";
import type { ContentWorkflow } from "../../../types/content-workflow";
import type { PlatformAccount } from "../../../types";

const { Text, Title, Paragraph } = Typography;

type ManualReference = { title: string; content: string; note_url: string; likes: number | null; collects: number | null; comments: number | null };

function emptyReference(): ManualReference {
  return { title: "", content: "", note_url: "", likes: null, collects: null, comments: null };
}

function errorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { status?: number; data?: { detail?: string | { message?: string } } } }).response;
    const detail = response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (response?.status) return `请求失败（HTTP ${response.status}），请稍后重试。`;
  }
  if (typeof error === "object" && error !== null && "code" in error && error.code === "ECONNABORTED") return "请求超时，请稍后重试。";
  return "操作失败，请稍后重试。";
}

function stepOf(stage: ContentWorkflow["stage"]): number {
  if (stage === "created" || stage === "researched") return 0;
  if (stage === "topics_ready" || stage === "topic_selected") return 1;
  if (stage === "draft_ready" || stage === "draft_approved") return 2;
  return 3;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function XhsContentWorkflowPage() {
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [items, setItems] = useState<ContentWorkflow[]>([]);
  const [active, setActive] = useState<ContentWorkflow | null>(null);
  const [topic, setTopic] = useState("");
  const [sourceMode, setSourceMode] = useState<"manual" | "account">("manual");
  const [manualReferences, setManualReferences] = useState<ManualReference[]>([]);
  const [accountId, setAccountId] = useState<number | undefined>();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [feedback, setFeedback] = useState("");
  const [cardIndex, setCardIndex] = useState(0);
  const [cardHeadline, setCardHeadline] = useState("");
  const [cardBody, setCardBody] = useState("");
  const [cardVisualPrompt, setCardVisualPrompt] = useState("");
  const [imageUrls, setImageUrls] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [workingMessage, setWorkingMessage] = useState("");
  const [error, setError] = useState("");
  const requestSeq = useRef(0);

  useEffect(() => {
    let cancelled = false;
    contentWorkflowApi.list()
      .then((workflows) => {
        if (cancelled) return;
        setItems(workflows);
        setActive(workflows[0] ?? null);
      })
      .catch((reason: unknown) => { if (!cancelled) setError(errorMessage(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    fetchAccounts("xhs").then((accountList) => {
      if (cancelled) return;
      const pcAccounts = accountList.filter((account) => account.sub_type === "pc");
      setAccounts(pcAccounts);
      setAccountId(pcAccounts[0]?.id);
    }).catch(() => { /* 手动参考不依赖账号列表。 */ });
    return () => { cancelled = true; requestSeq.current += 1; };
  }, []);

  useEffect(() => {
    setTitle(active?.draft_title ?? "");
    setBody(active?.draft_body ?? "");
    setCardIndex(0);
  }, [active?.id, active?.draft_title, active?.draft_body]);

  useEffect(() => {
    const card = active?.cards[cardIndex];
    setCardHeadline(card?.headline ?? "");
    setCardBody(card?.body ?? "");
    setCardVisualPrompt(card?.visual_prompt ?? card?.headline ?? "");
  }, [active?.id, active?.version, cardIndex]);

  useEffect(() => {
    let cancelled = false;
    let urls: string[] = [];
    if (active?.stage === "cards_ready") {
      setImageUrls([]);
      Promise.allSettled(active.cards.map((_, index) => contentWorkflowApi.image(active.id, index, active.version)))
        .then((results) => {
          urls = results.map((result) => result.status === "fulfilled" ? URL.createObjectURL(result.value) : "");
          if (cancelled) urls.filter(Boolean).forEach(URL.revokeObjectURL);
          else {
            setImageUrls(urls);
            const missing = results.flatMap((result, index) => result.status === "rejected" ? [index + 1] : []);
            if (missing.length) setError(`第 ${missing.join("、")} 张图片预览加载失败，其余图片已显示。请刷新页面重试。`);
          }
        })
        .catch(() => { if (!cancelled) setError("图片预览加载失败，可尝试重新生成。 "); });
    } else {
      setImageUrls([]);
    }
    return () => { cancelled = true; urls.forEach(URL.revokeObjectURL); };
  }, [active?.id, active?.version, active?.stage]);

  async function run(action: () => Promise<ContentWorkflow>, success: string): Promise<void> {
    if (working) return;
    const seq = ++requestSeq.current;
    setWorking(true);
    setWorkingMessage(["图片已生成", "图片已重做", "背景已重绘"].includes(success) ? "火山方舟正在生成图片，单张可能需要数分钟；全部重绘会逐张生成，请等待完成。" : "");
    setError("");
    try {
      const next = await action();
      if (seq !== requestSeq.current) return;
      setActive(next);
      setItems((old) => [next, ...old.filter((item) => item.id !== next.id)]);
      if (success) message.success(success);
    } catch (reason) {
      if (seq === requestSeq.current) {
        setError(errorMessage(reason));
        if (active) {
          const latest = await contentWorkflowApi.get(active.id).catch(() => null);
          if (latest && seq === requestSeq.current) setActive(latest);
        }
      }
    } finally {
      if (seq === requestSeq.current) setWorking(false);
    }
  }

  async function create(): Promise<void> {
    const clean = topic.trim();
    if (clean.length < 2 || clean.length > 120) {
      setError("请输入 2–120 字主题。");
      return;
    }
    if (sourceMode === "account" && !accountId) {
      setError("请选择已绑定的小红书 PC 账号。");
      return;
    }
    if (sourceMode === "manual") {
      if (manualReferences.some((note) => !note.title.trim() || !note.content.trim())) {
        setError("每条参考笔记都需要标题和内容。");
        return;
      }
      if (manualReferences.some((note) => note.note_url.trim() && !/^https?:\/\/[^\s/]+/i.test(note.note_url.trim()))) {
        setError("参考链接需为完整的 HTTP 或 HTTPS 地址。");
        return;
      }
    }
    if (working) return;
    const seq = ++requestSeq.current;
    setWorking(true);
    setError("");
    try {
      const created = sourceMode === "manual"
        ? await contentWorkflowApi.createManual(clean, manualReferences.map((note) => ({ ...note, title: note.title.trim(), content: note.content.trim(), note_url: note.note_url.trim() })))
        : await contentWorkflowApi.create(clean, accountId!);
      if (seq !== requestSeq.current) return;
      setActive(created);
      setItems((old) => [created, ...old]);
      if (sourceMode === "manual") {
        message.success(manualReferences.length ? "参考笔记已保存，可以生成选题" : "主题已保存，可以直接生成选题");
        return;
      }
      const researched = await contentWorkflowApi.research(created.id, created.version);
      if (seq !== requestSeq.current) return;
      setActive(researched);
      setItems((old) => [researched, ...old.filter((item) => item.id !== researched.id)]);
      message.success("已获取热门内容");
    } catch (reason) {
      if (seq === requestSeq.current) setError(errorMessage(reason));
    } finally {
      if (seq === requestSeq.current) setWorking(false);
    }
  }

  async function download(): Promise<void> {
    if (!active || working) return;
    setWorking(true);
    setError("");
    try {
      triggerDownload(await contentWorkflowApi.export(active.id), `content-${active.id}.zip`);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setWorking(false); }
  }

  function select(item: ContentWorkflow): void {
    requestSeq.current += 1;
    setActive(item);
    setWorking(false);
    setError("");
  }

  function updateReference(index: number, change: Partial<ManualReference>): void {
    setManualReferences((old) => old.map((note, position) => position === index ? { ...note, ...change } : note));
  }

  const step = active ? stepOf(active.stage) : 0;

  return (
    <div>
      <PageHeader eyebrow="AI CONTENT" title="内容策划" description="输入主题即可开始，也可以补充参考笔记；逐步生成选题、公众号长文和小红书图片。" />
      {error && <Alert type="error" showIcon message={error} closable onClose={() => setError("")} style={{ marginBottom: 16 }} />}
      <Card style={{ marginBottom: 20 }}>
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Segmented value={sourceMode} onChange={(value) => setSourceMode(value as "manual" | "account")} options={[{ label: "主题策划（参考可选）", value: "manual" }, { label: "账号自动获取", value: "account" }]} />
          <Input placeholder="输入主题，例如：新手如何做个人知识管理" value={topic} onChange={(event) => setTopic(event.target.value)} maxLength={120} style={{ width: 500, maxWidth: "100%" }} />
          {sourceMode === "manual" ? <>
            <Text type="secondary">参考笔记可不填，直接按主题生成 AI 选题。若添加笔记，请填写标题和内容；链接只记录来源，不会自动抓取。</Text>
            {manualReferences.map((note, index) => <Card key={index} size="small" title={`参考笔记 ${index + 1}`} extra={<Button type="link" danger onClick={() => setManualReferences((old) => old.filter((_, position) => position !== index))}>删除</Button>}>
              <Space direction="vertical" style={{ width: "100%" }}>
                <Input aria-label={`参考笔记 ${index + 1} 标题`} placeholder="笔记标题（必填）" value={note.title} maxLength={200} onChange={(event) => updateReference(index, { title: event.target.value })} />
                <Input.TextArea aria-label={`参考笔记 ${index + 1} 内容`} placeholder="笔记正文或你整理的核心内容（必填）" value={note.content} rows={4} maxLength={3000} showCount onChange={(event) => updateReference(index, { content: event.target.value })} />
                <Input aria-label={`参考笔记 ${index + 1} 链接`} placeholder="原文链接（选填，需以 http:// 或 https:// 开头）" value={note.note_url} maxLength={2048} onChange={(event) => updateReference(index, { note_url: event.target.value })} />
                <Space wrap>
                  <InputNumber aria-label={`参考笔记 ${index + 1} 点赞数`} min={0} precision={0} placeholder="点赞" value={note.likes} onChange={(value) => updateReference(index, { likes: value })} />
                  <InputNumber aria-label={`参考笔记 ${index + 1} 收藏数`} min={0} precision={0} placeholder="收藏" value={note.collects} onChange={(value) => updateReference(index, { collects: value })} />
                  <InputNumber aria-label={`参考笔记 ${index + 1} 评论数`} min={0} precision={0} placeholder="评论" value={note.comments} onChange={(value) => updateReference(index, { comments: value })} />
                </Space>
              </Space>
            </Card>)}
            <Space wrap><Button disabled={manualReferences.length >= 5 || working} onClick={() => setManualReferences((old) => [...old, emptyReference()])}>添加参考笔记</Button><Button type="primary" loading={working} onClick={() => void create()}>开始策划</Button></Space>
          </> : <>
            <Space wrap><Select placeholder="选择小红书 PC 账号" value={accountId} onChange={setAccountId} style={{ width: 220 }} options={accounts.map((account) => ({ value: account.id, label: account.nickname || `账号 ${account.id}` }))} /><Button type="primary" loading={working} disabled={!accountId} onClick={() => void create()}>获取热门内容</Button></Space>
            {accounts.length === 0 && !loading && <Text type="secondary">自动获取需要先在账号矩阵绑定小红书 PC 账号。你也可以使用上方的免绑定入口。</Text>}
          </>}
        </Space>
      </Card>
      {loading ? <Spin /> : (
        <Row gutter={[20, 20]}>
          <Col xs={24} lg={6}>
            <Card title="历史任务" styles={{ body: { padding: 8 } }}>
              <List dataSource={items} locale={{ emptyText: "暂无内容任务" }} renderItem={(item) => (
                <List.Item onClick={() => select(item)} style={{ cursor: "pointer", padding: 12, background: active?.id === item.id ? "rgba(22,104,220,.12)" : undefined, borderRadius: 8 }}>
                  <div style={{ width: "100%" }}><Text strong>{item.topic}</Text> <Tag>{item.source_mode === "manual" ? (item.references.length ? "手动参考" : "仅主题") : "账号采集"}</Tag><br /><Text type="secondary" style={{ fontSize: 12 }}>{new Date(item.updated_at).toLocaleString("zh-CN")}</Text></div>
                </List.Item>
              )} />
            </Card>
          </Col>
          <Col xs={24} lg={18}>
            {!active ? <Card><Empty description="输入主题，开始内容策划" /></Card> : (
              <Card title={active.topic} extra={<Tag>版本 {active.version}</Tag>}>
                <Steps current={step} size="small" items={[{ title: "参考资料" }, { title: "AI 选题" }, { title: "文案审核" }, { title: "图片与导出" }]} style={{ marginBottom: 28 }} />
                {step === 0 && <>
                  <Space style={{ marginBottom: 16 }}>
                    {active.stage === "created" && active.source_mode === "account" && <Button type="primary" loading={working} onClick={() => void run(() => contentWorkflowApi.research(active.id, active.version), "采集完成")}>重新获取热门内容</Button>}
                    {active.stage === "researched" && <Button type="primary" loading={working} onClick={() => void run(() => contentWorkflowApi.topics(active.id, active.version), "选题已生成")}>生成 3–5 个选题</Button>}
                  </Space>
                  <List dataSource={active.references} locale={{ emptyText: active.source_mode === "manual" ? "未提供参考笔记。可直接生成选题，但选题热度未经验证。" : "暂无热门参考" }} renderItem={(note) => <List.Item><List.Item.Meta title={note.note_url ? <a href={note.note_url} target="_blank" rel="noreferrer">{note.title || "未命名笔记"}</a> : note.title} description={<><Paragraph ellipsis={{ rows: 2 }}>{note.content}</Paragraph><Text type="secondary">{[note.likes !== null ? `赞 ${note.likes}` : null, note.collects !== null ? `收藏 ${note.collects}` : null, note.comments !== null ? `评论 ${note.comments}` : null].filter(Boolean).join(" · ") || "未填写互动数据"}</Text></>} /></List.Item>} />
                </>}
                {step === 1 && <>
                  {active.stage === "topic_selected" ? <Alert type="success" message={`已选：${active.selected_topic.title}`} style={{ marginBottom: 16 }} /> : <Text type="secondary">选择一个子选题后再生成公众号文案。</Text>}
                  <Row gutter={[12, 12]} style={{ marginTop: 16 }}>{active.candidates.map((candidate, index) => <Col xs={24} md={12} key={`${candidate.title}-${index}`}><Card size="small" title={candidate.title} extra={active.stage === "topics_ready" ? <Button size="small" type="primary" disabled={working} onClick={() => void run(() => contentWorkflowApi.selectTopic(active.id, active.version, index), "选题已确认")}>采用</Button> : null}><Paragraph>面向：{candidate.audience}</Paragraph><Paragraph>角度：{candidate.angle}</Paragraph><Paragraph>差异：{candidate.difference}</Paragraph><Text type="secondary">依据：{candidate.evidence}</Text></Card></Col>)}</Row>
                  {active.stage === "topic_selected" && <Button type="primary" loading={working} style={{ marginTop: 18 }} onClick={() => void run(() => contentWorkflowApi.generateDraft(active.id, active.version, ""), "文案已生成")}>生成公众号文案</Button>}
                </>}
                {step === 2 && <>
                  <Title level={5}>公众号文案</Title>
                  <Input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={300} placeholder="标题" style={{ marginBottom: 12 }} disabled={active.stage === "draft_approved"} />
                  <Input.TextArea value={body} onChange={(event) => setBody(event.target.value)} rows={18} maxLength={15000} disabled={active.stage === "draft_approved"} />
                  {active.stage === "draft_ready" ? <Space direction="vertical" style={{ width: "100%", marginTop: 16 }}>
                    <Input.TextArea value={feedback} onChange={(event) => setFeedback(event.target.value)} rows={2} maxLength={1000} placeholder="修改意见，例如：开头更具体，减少术语" />
                    <Space wrap>
                      <Select placeholder="查看历史版本" style={{ width: 170 }} options={active.draft_versions.map((version, index) => ({ value: index, label: `版本 ${index + 1} · ${version.source === "ai" ? "AI" : "人工"}` }))} onChange={(index: number) => { const previous = active.draft_versions[index]; setTitle(previous.title); setBody(previous.body); }} />
                      <Button disabled={working || !title.trim() || !body.trim()} onClick={() => void run(() => contentWorkflowApi.saveDraft(active.id, active.version, title, body), "文案已保存")}>保存人工修改</Button>
                      <Button loading={working} onClick={() => void run(() => contentWorkflowApi.generateDraft(active.id, active.version, feedback), "文案已重写")}>按意见重新生成</Button>
                      <Button type="primary" disabled={working || title !== active.draft_title || body !== active.draft_body} onClick={() => void run(() => contentWorkflowApi.approveDraft(active.id, active.version), "文案已确认")}>确认文案</Button>
                    </Space>
                    {active.draft_versions.length > 0 && <Text type="secondary">已保存 {active.draft_versions.length} 个版本</Text>}
                  </Space> : <Button type="primary" loading={working} style={{ marginTop: 16 }} onClick={() => void run(() => contentWorkflowApi.generateCards(active.id, active.version), "图片已生成")}>生成 3–5 张图片</Button>}
                </>}
                {step === 3 && <>
                  {working && workingMessage && <Alert type="info" showIcon icon={<Spin size="small" />} message={workingMessage} style={{ marginBottom: 16 }} />}
                  <Space wrap style={{ marginBottom: 18 }}><Button type="primary" loading={working} onClick={() => void download()}>下载双平台发布包</Button><Button disabled={working} onClick={() => void run(() => contentWorkflowApi.generateCards(active.id, active.version), "图片已重做")}>重新生成全部图片</Button><Button onClick={() => void navigator.clipboard.writeText(active.draft_body)}>复制公众号正文</Button><Button onClick={() => void navigator.clipboard.writeText(`${active.xhs_title}\n\n${active.xhs_caption}`)}>复制小红书文案</Button></Space>
                  <Row gutter={[12, 12]}>{active.cards.map((card, index) => <Col xs={12} md={8} key={index}><Card size="small" hoverable onClick={() => setCardIndex(index)} style={{ borderColor: cardIndex === index ? "#1668dc" : undefined }} cover={imageUrls[index] ? <img src={imageUrls[index]} alt={`第 ${index + 1} 张：${card.headline}`} style={{ width: "100%" }} /> : undefined}><Text>{card.headline}</Text></Card></Col>)}</Row>
                  <Card title={`编辑第 ${cardIndex + 1} 张`} size="small" style={{ marginTop: 20 }}><Space direction="vertical" style={{ width: "100%" }}><Input value={cardHeadline} onChange={(event) => setCardHeadline(event.target.value)} maxLength={50} placeholder="卡片标题" /><Input.TextArea value={cardBody} onChange={(event) => setCardBody(event.target.value)} rows={3} maxLength={300} placeholder="卡片正文" /><Button disabled={working || !cardHeadline.trim() || !cardBody.trim()} onClick={() => void run(() => contentWorkflowApi.updateCard(active.id, cardIndex, active.version, cardHeadline, cardBody), "文字已更新")}>保存文字并重新排版</Button><Text strong>Seedream 视觉提示词</Text><Input.TextArea value={cardVisualPrompt} onChange={(event) => setCardVisualPrompt(event.target.value)} rows={3} maxLength={200} showCount placeholder="描述这张图要呈现的具体场景和主体，不需要写标题文字" /><Text type="secondary">重绘背景会再次调用 Seedream 图片接口；已保存的卡片文字会保留。</Text><Button disabled={working || cardVisualPrompt.trim().length < 5} onClick={() => void run(() => contentWorkflowApi.regenerateCardVisual(active.id, cardIndex, active.version, cardVisualPrompt), "背景已重绘")}>用提示词重绘这张背景</Button></Space></Card>
                </>}
              </Card>
            )}
          </Col>
        </Row>
      )}
    </div>
  );
}
