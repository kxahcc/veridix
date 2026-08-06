import { useState } from "react";
import { Upload } from "lucide-react";
import { CONTROL_URL } from "../api.js";
import { Notice } from "./ui.js";

export function JsonImportForm({
  title,
  endpoint,
  onImported,
  placeholder,
}: {
  title: string;
  endpoint: string;
  onImported?: () => void;
  placeholder?: string;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [pending, setPending] = useState(false);

  const doImport = async () => {
    setError("");
    setSuccess("");
    let raw = text;
    if (file) {
      raw = await file.text();
    }
    if (!raw.trim()) {
      setError("请粘贴 JSON 或选择文件");
      return;
    }
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      setError("JSON 格式无效");
      return;
    }
    const items = Array.isArray(data)
      ? data
      : Array.isArray((data as { items?: unknown })?.items)
        ? (data as { items: unknown[] }).items
        : [data];
    if (!items.length) {
      setError("没有可导入的条目");
      return;
    }
    setPending(true);
    try {
      const response = await fetch(`${CONTROL_URL}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      if (!response.ok) {
        const body = await response.text();
        throw new Error(body || `HTTP ${response.status}`);
      }
      const result = (await response.json()) as { imported?: number };
      setSuccess(`已导入 ${result.imported ?? items.length} 条`);
      setText("");
      setFile(null);
      onImported?.();
    } catch (err) {
      setError(String(err));
    } finally {
      setPending(false);
    }
  };

  return (
    <details className="registry-form">
      <summary>{title}</summary>
      <p className="muted" style={{ margin: "6px 0 10px", fontSize: 12 }}>
        支持粘贴 JSON 数组，或上传一个包含数组/单对象的 JSON 文件。
      </p>
      <label className="field">
        JSON 文件
        <input
          type="file"
          accept=".json,application/json"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <label className="field" style={{ marginTop: 8 }}>
        JSON 内容
        <textarea
          rows={6}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={placeholder ?? '[{"id":"item-1"}]'}
        />
      </label>
      <div className="btn-group" style={{ marginTop: 10 }}>
        <button
          className="btn btn-primary"
          onClick={() => void doImport()}
          disabled={pending}
        >
          <Upload className="" />
          {pending ? "导入中..." : "导入"}
        </button>
      </div>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {success ? <Notice tone="ok">{success}</Notice> : null}
    </details>
  );
}
