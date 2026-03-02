/**
 * ImportProjectModal -- multi-step modal for importing a project.
 * Step 1: Enter path -> validate
 * Step 2: Show validation results + optional overrides -> confirm import
 * Step 3: Show import result (success/warnings)
 */

import { useCallback, useState } from "react";
import { ApiError, importProject, validateProject } from "../api";
import type { ImportResult, ValidationResult } from "../types";
import DirectoryPicker from "./DirectoryPicker";

interface ImportProjectModalProps {
  onClose: () => void;
  onImported: () => void;
  onError: (msg: string) => void;
}

type Step = "input" | "review" | "done";

export default function ImportProjectModal({
  onClose,
  onImported,
  onError,
}: ImportProjectModalProps) {
  const [step, setStep] = useState<Step>("input");
  const [path, setPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  // Browse mode toggle
  const [browsing, setBrowsing] = useState(false);

  // Override fields for import
  const [nameOverride, setNameOverride] = useState("");
  const [projectType, setProjectType] = useState("other");
  const [launchCommand, setLaunchCommand] = useState("");
  const [preferredPort, setPreferredPort] = useState("");

  const handleValidate = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!path.trim()) {
        setError("Path is required");
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const result = await validateProject(path.trim());
        setValidation(result);
        if (!result.valid) {
          setError("Directory is not valid for import");
        } else {
          setNameOverride(result.name);
          setStep("review");
        }
      } catch (err) {
        const msg =
          err instanceof ApiError ? err.detail : "Validation failed";
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [path],
  );

  const handleImport = useCallback(async () => {
    if (!validation) return;

    setLoading(true);
    setError(null);
    try {
      const result = await importProject({
        path: validation.path,
        project_id: validation.suggested_id,
        name: nameOverride.trim() || undefined,
        project_type: projectType,
        launch_command: launchCommand.trim() || undefined,
        preferred_port: preferredPort ? parseInt(preferredPort, 10) : undefined,
      });
      setImportResult(result);
      setStep("done");
      onImported();
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Import failed";
      setError(msg);
      onError(msg);
    } finally {
      setLoading(false);
    }
  }, [validation, nameOverride, projectType, launchCommand, preferredPort, onImported, onError]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900">Import Project</h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-lg leading-none"
          >
            x
          </button>
        </div>

        <div className="p-5">
          {/* Step 1: Path input (text or browse) */}
          {step === "input" && (
            <>
              {browsing ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <label className="block text-xs font-medium text-gray-700">
                      Browse for project directory
                    </label>
                    <button
                      type="button"
                      onClick={() => setBrowsing(false)}
                      className="text-xs text-indigo-600 hover:text-indigo-800"
                    >
                      Type path instead
                    </button>
                  </div>
                  <DirectoryPicker
                    onSelect={(selectedPath) => {
                      setPath(selectedPath);
                      setBrowsing(false);
                    }}
                    onCancel={() => setBrowsing(false)}
                  />
                </div>
              ) : (
                <form onSubmit={handleValidate} className="space-y-4">
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="block text-xs font-medium text-gray-700">
                        Project directory path
                      </label>
                      <button
                        type="button"
                        onClick={() => setBrowsing(true)}
                        className="text-xs text-indigo-600 hover:text-indigo-800"
                      >
                        Browse...
                      </button>
                    </div>
                    <input
                      type="text"
                      value={path}
                      onChange={(e) => setPath(e.target.value)}
                      placeholder="C:\Users\...\my-project  or  ~/projects/my-project"
                      className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                      autoFocus
                      disabled={loading}
                    />
                  </div>

                  {/* Show validation errors if directory was invalid */}
                  {validation && !validation.valid && (
                    <div className="text-xs text-red-600 bg-red-50 px-3 py-2 rounded space-y-1">
                      <p className="font-medium">Cannot import this directory:</p>
                      {validation.limited_mode_reasons.map((r, i) => (
                        <p key={i}>- {r}</p>
                      ))}
                    </div>
                  )}

                  {error && !validation && (
                    <p className="text-xs text-red-600 bg-red-50 px-3 py-2 rounded">
                      {error}
                    </p>
                  )}

                  <div className="flex items-center justify-end gap-2 pt-1">
                    <button
                      type="button"
                      onClick={onClose}
                      className="rounded-md px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors"
                      disabled={loading}
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={loading || !path.trim()}
                      className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {loading ? "Validating..." : "Validate"}
                    </button>
                  </div>
                </form>
              )}
            </>
          )}

          {/* Step 2: Review validation + configure import */}
          {step === "review" && validation && (
            <div className="space-y-4">
              {/* Validation summary */}
              <div className="bg-gray-50 rounded-md p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-gray-700">Path:</span>
                  <span className="text-xs text-gray-600 font-mono truncate">
                    {validation.path}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  <span
                    className={`px-1.5 py-0.5 rounded ${
                      validation.has_git
                        ? "bg-green-100 text-green-700"
                        : "bg-yellow-100 text-yellow-700"
                    }`}
                  >
                    {validation.has_git ? "Git" : "No Git"}
                  </span>
                  <span
                    className={`px-1.5 py-0.5 rounded ${
                      validation.has_tasks_md
                        ? "bg-green-100 text-green-700"
                        : "bg-yellow-100 text-yellow-700"
                    }`}
                  >
                    {validation.has_tasks_md ? "TASKS.md" : "No TASKS.md"}
                  </span>
                  <span
                    className={`px-1.5 py-0.5 rounded ${
                      validation.has_claude_config
                        ? "bg-green-100 text-green-700"
                        : "bg-yellow-100 text-yellow-700"
                    }`}
                  >
                    {validation.has_claude_config ? "CLAUDE.md" : "No CLAUDE.md"}
                  </span>
                </div>
                <div className="text-xs text-gray-500">
                  ID: <span className="font-mono">{validation.suggested_id}</span>
                </div>
              </div>

              {/* Warnings */}
              {(validation.warnings.length > 0 ||
                validation.limited_mode_reasons.length > 0) && (
                <div className="text-xs space-y-1">
                  {validation.warnings.map((w, i) => (
                    <p key={`w-${i}`} className="text-yellow-700 bg-yellow-50 px-2 py-1 rounded">
                      [WARN] {w}
                    </p>
                  ))}
                  {validation.limited_mode_reasons.map((r, i) => (
                    <p key={`l-${i}`} className="text-orange-700 bg-orange-50 px-2 py-1 rounded">
                      [LIMITED] {r}
                    </p>
                  ))}
                </div>
              )}

              {/* Override fields */}
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">
                    Display name
                  </label>
                  <input
                    type="text"
                    value={nameOverride}
                    onChange={(e) => setNameOverride(e.target.value)}
                    className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                    disabled={loading}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Project type
                    </label>
                    <select
                      value={projectType}
                      onChange={(e) => setProjectType(e.target.value)}
                      className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 bg-white"
                      disabled={loading}
                    >
                      <option value="frontend">Frontend</option>
                      <option value="backend">Backend</option>
                      <option value="other">Other</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Preferred port
                    </label>
                    <input
                      type="number"
                      value={preferredPort}
                      onChange={(e) => setPreferredPort(e.target.value)}
                      placeholder="Auto"
                      min={1024}
                      max={65535}
                      className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                      disabled={loading}
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">
                    Launch command (optional)
                  </label>
                  <input
                    type="text"
                    value={launchCommand}
                    onChange={(e) => setLaunchCommand(e.target.value)}
                    placeholder="e.g. npm run dev"
                    className="w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                    disabled={loading}
                  />
                </div>
              </div>

              {error && (
                <p className="text-xs text-red-600 bg-red-50 px-3 py-2 rounded">
                  {error}
                </p>
              )}

              <div className="flex items-center justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => {
                    setStep("input");
                    setError(null);
                  }}
                  className="rounded-md px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors"
                  disabled={loading}
                >
                  Back
                </button>
                <button
                  onClick={handleImport}
                  disabled={loading}
                  className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {loading ? "Importing..." : "Import"}
                </button>
              </div>
            </div>
          )}

          {/* Step 3: Done */}
          {step === "done" && importResult && (
            <div className="space-y-4">
              <div className="bg-green-50 rounded-md p-3 text-sm text-green-800">
                <p className="font-medium">
                  Project imported: {importResult.name}
                </p>
                <p className="text-xs mt-1 text-green-700 font-mono">
                  ID: {importResult.project_id}
                </p>
                {importResult.port && (
                  <p className="text-xs mt-1 text-green-700">
                    Port: {importResult.port}
                  </p>
                )}
                {importResult.synced && importResult.sync_result && (
                  <p className="text-xs mt-1 text-green-700">
                    Synced: {importResult.sync_result.added} added,{" "}
                    {importResult.sync_result.updated} updated
                  </p>
                )}
              </div>

              {importResult.warnings.length > 0 && (
                <div className="text-xs space-y-1">
                  {importResult.warnings.map((w, i) => (
                    <p key={i} className="text-yellow-700 bg-yellow-50 px-2 py-1 rounded">
                      [WARN] {w}
                    </p>
                  ))}
                </div>
              )}

              <div className="flex items-center justify-end pt-1">
                <button
                  onClick={onClose}
                  className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 transition-colors"
                >
                  Done
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
