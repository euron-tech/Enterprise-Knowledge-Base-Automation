import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../../auth/AuthContext";
import {
  listDocuments,
  uploadDocument,
  type DocumentSummary,
  type UploadResult,
} from "../../api/client";

export function Documents() {
  const { identity } = useAuth();
  const [docs, setDocs] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [department, setDepartment] = useState(identity?.departments[0] ?? "");
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setDocs((await listDocuments()).documents);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load documents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file || !department) return;
    setUploading(true);
    setResult(null);
    setError(null);
    try {
      const r = await uploadDocument(file, department);
      setResult(r);
      if (fileRef.current) fileRef.current.value = "";
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  const byDept = docs.reduce<Record<string, DocumentSummary[]>>((acc, d) => {
    (acc[d.department] ??= []).push(d);
    return acc;
  }, {});

  return (
    <div className="page">
      <div className="page-head">
        <h1>Documents</h1>
        <p className="page-sub">
          Only documents in your tenant and granted departments appear here. This is the
          same scope the answer engine searches.
        </p>
      </div>

      <section className="card">
        <h2 className="section-title">Add a document</h2>
        <div className="upload-row">
          <select
            value={department}
            onChange={(e) => setDepartment(e.target.value)}
            aria-label="Department"
            disabled={uploading}
          >
            {identity?.departments.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <input
            ref={fileRef}
            type="file"
            aria-label="File"
            disabled={uploading}
            accept=".pdf,.txt,.md,.csv,.html,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.mp3,.wav,.mp4"
          />
          <button
            className="btn btn-primary"
            onClick={upload}
            disabled={uploading || !department}
          >
            {uploading ? "Ingesting…" : "Upload"}
          </button>
        </div>
        <p className="hint">
          Content type is verified from the file&rsquo;s bytes, not its extension. Anything
          that reads as an instruction to the assistant is quarantined, not indexed.
        </p>

        {result && (
          <div className={`upload-result ${result.status === "duplicate" ? "dup" : "ok"}`}>
            <strong>
              {result.status === "duplicate"
                ? "Already ingested — identical content"
                : `Ingested: ${result.chunks_written} chunks across ${result.pages} page(s)`}
            </strong>
            {result.quarantined_elements > 0 && (
              <span className="quarantine">
                {result.quarantined_elements} element(s) quarantined for embedded
                instructions
              </span>
            )}
          </div>
        )}
      </section>

      {error && (
        <div className="card error" role="alert">
          {error}
        </div>
      )}

      {loading ? (
        <div className="card muted">Loading…</div>
      ) : docs.length === 0 ? (
        <div className="card muted">No documents in your scope yet. Upload one above.</div>
      ) : (
        Object.entries(byDept).map(([dept, list]) => (
          <section className="card" key={dept}>
            <h2 className="section-title">
              {dept} <span className="count">{list.length}</span>
            </h2>
            <div className="scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Version</th>
                    <th>Pages</th>
                    <th>Document id</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((d) => (
                    <tr key={d.document_id}>
                      <td>{d.name}</td>
                      <td className="mono">v{d.version}</td>
                      <td className="mono">{d.pages}</td>
                      <td className="mono subtle">{d.document_id.slice(0, 8)}…</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
    </div>
  );
}
