// ui/backups.js - Keep private capture, exact inspection and restored read-only state as separate actions.
"use strict";


// Retain chosen paths and one inspected extent in memory; no background capture or upload
let backupBusy = false;
let inspectedBackup = null;


// Prevent duplicate submissions after native validation, without replacing authored paths on failure
async function backupRun(work) {
  if (backupBusy) return;
  backupBusy = true;
  const focused = document.activeElement;
  const buttons = [...element('backup-panel').querySelectorAll('button')];
  buttons.forEach(node => { node.disabled = true; });
  try {
    await work();
    element('backup-status').className = '';
  } catch (error) {
    element('backup-status').textContent = error.message;
    element('backup-status').className = 'error';
  } finally {
    backupBusy = false;
    buttons.forEach(node => { node.disabled = false; });
    if (element('backup-panel').open && document.activeElement === document.body && focused?.isConnected) focused.focus();
  }
}


// Fill only empty controls so a late response cannot erase an operator's directory choices
async function backupOptions() {
  const data = await api('/backups/options');
  for (const [id, value] of [['backup-directory', data.backup_directory], ['restore-workspace', data.workspace_directory]]) {
    if (!element(id).value) element(id).value = value;
  }
  element('backup-limits').textContent = 'Capture limits: ' + data.max_entries + ' entries, ' +
    Math.floor(data.max_bytes / 1000000) + ' MB total and ' + Math.floor(data.max_file_bytes / 1000000) +
    ' MB per file/database. Restore stays inspection-only.';
}


// Display completeness and exact audit extent without rendering raw database or credential contents
function showBackup(result) {
  if (!(result?.manifest && result.verified === true) && result?.status !== 'restored-for-inspection') {
    throw new Error('No verified backup or completed restore was returned. Keep the previous files and inspect the operation.');
  }
  const root = element('backup-detail'); root.replaceChildren();
  if (result.manifest && result.verified) {
    const manifest = result.manifest;
    inspectedBackup = {directory: result.bundle, digest: manifest.sha256, id: manifest.id};
    element('restore-backup').value = result.bundle;
    element('restore-digest').value = manifest.sha256;
    text(root, 'h4', 'Verified private backup');
    reviewFacts(root, [['Capture completeness', manifest.capture], ['Backup identity', manifest.id],
      ['Captured toolkit', manifest.toolkit_version], ['Directory', result.bundle], ['Releases', manifest.release_count],
      ['Files', manifest.files.length], ['Audit events', manifest.audit.events], ['Audit head', manifest.audit.head],
      ['Manifest SHA-256', manifest.sha256]]);
    text(root, 'p', 'Verified bytes and retained audit lineage do not establish current target health or permission to resume.');
    reviewEvidence(root, 'backup-missing', 'Missing and unavailable retained evidence',
      {missing: manifest.missing, unavailable: manifest.unavailable}, new Set());
    reviewEvidence(root, 'backup-omitted', 'Explicitly omitted scope', manifest.omitted, new Set());
    reviewEvidence(root, 'backup-files', 'Exact captured file hashes', manifest.files, new Set());
    element('backup-status').textContent = 'Backup verified. Capture is ' + manifest.capture +
      '. This exact extent is selected for a separate inspection-only restore.';
    return;
  }
  if (result.status === 'restored-for-inspection') {
    text(root, 'h4', 'Restored for inspection');
    reviewFacts(root, [['Workspace file', result.project], ['State directory', result.state],
      ['Original locations', result.original_location ? 'Yes' : 'No; relocated inspection copy'],
      ['Capture completeness', result.capture], ['Verified files', result.file_count], ['Audit head', result.audit.head], ['Execution admitted', 'No']]);
    text(root, 'p', 'Paths, approvals and historical rows were kept as recorded. No worker was restarted. Use CLI status/events with this state directory. Exact retained static readback uses preview --retained and still requires matching original paths and compatible files.');
    reviewEvidence(root, 'restore-unavailable', 'Retained evidence limits', {missing: result.missing, unavailable: result.unavailable}, new Set());
    element('backup-status').textContent = 'Restored copy verified for inspection. Execution remains blocked; no paths or digests were rewritten.';
    return;
  }
}


// Native submit handles Enter and validation; read-only inspection is the first default action
element('backup-form').onsubmit = event => {
  event.preventDefault();
  const action = event.submitter?.value === 'capture' ? 'capture' : 'inspect';
  const directory = element('backup-directory').value;
  return backupRun(async () => {
    element('backup-status').textContent = action === 'capture'
      ? 'Capturing a private snapshot and verifying owned files…' : 'Verifying the selected backup extent…';
    showBackup(await api('/backups/' + action, 'POST', {backup_directory: directory}));
  });
};


// Bind the selected inspected extent and explicit new targets; preserve drafts when confirmation is cancelled
element('restore-form').onsubmit = event => {
  event.preventDefault();
  const action = event.submitter?.value === 'inspect-restored' ? 'inspect-restored' : 'restore';
  const selected = inspectedBackup && {...inspectedBackup};
  const workspace = element('restore-workspace').value;
  const state = element('restore-state').value;
  return backupRun(async () => {
    if (action === 'inspect-restored') {
      element('backup-status').textContent = 'Verifying the restored receipt, files and audit extent…';
      showBackup(await api('/backups/inspect-restored', 'POST', {workspace_directory: workspace, state_directory: state}));
      return;
    }
    if (!selected) throw new Error('Inspect a backup first to select its exact identity and digest.');
    if (!await confirmAction('Restore backup ' + selected.id + ' into new workspace ' + workspace +
      ' and state ' + (state || '.deploy-ops inside that workspace') + '? Existing directories are refused. The copy remains inspection-only.')) return;
    element('backup-status').textContent = 'Restoring verified files into inspection-only state…';
    showBackup(await api('/backups/restore', 'POST', {backup_directory: selected.directory, digest: selected.digest,
      workspace_directory: workspace, state_directory: state}));
  });
};


// Suggestions are explicit and do not overwrite path drafts or trigger a filesystem operation
element('backup-options').onclick = () => backupRun(backupOptions);
element('backup-panel').ontoggle = () => {
  if (element('backup-panel').open && token && !element('backup-limits').textContent) backupRun(backupOptions);
};
