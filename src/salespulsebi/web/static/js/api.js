const BASE = "/api/v1";

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      msg = body.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

export const api = {
  listProjects: () => req("/projects"),
  createProject: (data) =>
    req("/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  getProject: (id) => req(`/projects/${id}`),
  updateProject: (id, data) =>
    req(`/projects/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }),
  deleteProject: (id) => req(`/projects/${id}`, { method: "DELETE" }),
  duplicateProject: (id) => req(`/projects/${id}/duplicate`, { method: "POST" }),
  importBundle: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return req("/projects/import-bundle", { method: "POST", body: fd });
  },

  uploadFiles: (projectId, fileList) => {
    const fd = new FormData();
    const relPaths = [];
    for (const f of fileList) {
      fd.append("files", f);
      relPaths.push(f.webkitRelativePath || f.name);
    }
    fd.append("relative_paths", JSON.stringify(relPaths));
    return req(`/projects/${projectId}/upload`, { method: "POST", body: fd });
  },
  linkFolder: (projectId, path, recursive = true) =>
    req(`/projects/${projectId}/link-folder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, recursive }),
    }),
  syncProject: (projectId) => req(`/projects/${projectId}/sync`, { method: "POST" }),
  removeSource: (projectId, sourceId) =>
    req(`/projects/${projectId}/sources/${sourceId}`, { method: "DELETE" }),
  browseFs: (path = "") => req(`/fs/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`),

  getMapping: (projectId) => req(`/projects/${projectId}/mapping`),
  updateMapping: (projectId, payload) =>
    req(`/projects/${projectId}/mapping`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  createSavedView: (projectId, payload) =>
    req(`/projects/${projectId}/views`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteSavedView: (projectId, viewId) =>
    req(`/projects/${projectId}/views/${viewId}`, { method: "DELETE" }),

  seedDemo: () => req("/demo/seed", { method: "POST" }),

  analytics: (projectId, endpoint, body) =>
    req(`/projects/${projectId}/analytics/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  async downloadExport(projectId, format, filters) {
    const res = await fetch(`${BASE}/projects/${projectId}/export/${format}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filters),
    });
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    const blob = await res.blob();
    const disp = res.headers.get("content-disposition") || "";
    const match = disp.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `salespulsebi_${format}`;

    if (window.pywebview?.api?.save_file) {
      const buf = await blob.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let bin = "";
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      await window.pywebview.api.save_file(filename, btoa(bin));
      return;
    }

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
