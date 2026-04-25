<script setup lang="ts">
import axios from "axios";
import { computed, onMounted, reactive, ref } from "vue";
import * as api from "./api";
import type { Recipe, Settings } from "./api";

const tab = ref<"status" | "recipes" | "settings" | "manual">("status");
const status = ref<Awaited<ReturnType<typeof api.getStatus>> | null>(null);
const settings = ref<Settings | null>(null);
const recipes = ref<Recipe[]>([]);
const msg = reactive({ text: "", err: false });
const busy = ref(false);
const nowMs = ref(Date.now());

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function actionLabel(a: string | null): string {
  switch (a) {
    case "snapshot_prior":
      return "Snapshotting current state";
    case "tilt":
      return "Setting tilt";
    case "light":
      return "Adjusting light";
    case "snapshot":
      return "Capturing snapshot";
    case "recording":
      return "Recording video";
    case "remux":
      return "Finalising video";
    case "restore":
      return "Restoring previous state";
    default:
      return "";
  }
}

function formatRelative(iso: string | null, fromMs: number): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const deltaMs = t - fromMs;
  const past = deltaMs < 0;
  const sec = Math.floor(Math.abs(deltaMs) / 1000);
  if (sec < 5) return "now";
  let out: string;
  if (sec < 60) {
    out = `${sec}s`;
  } else if (sec < 3600) {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    out = s ? `${m}m${s}s` : `${m}m`;
  } else if (sec < 86400) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    out = m ? `${h}h${m}m` : `${h}h`;
  } else {
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    out = h ? `${d}d${h}h` : `${d}d`;
  }
  return past ? `${out} ago` : `in ${out}`;
}

// The ISO already encodes the device tz offset, so we slice the date and HH:MM directly
// from the string and compute the weekday from the date portion via Date.UTC. This keeps
// the displayed weekday/HH:MM matching the device tz regardless of the browser tz.
function formatLocalShort(iso: string | null): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!m) return "";
  const [, y, mo, d, hh, mm] = m;
  const utc = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)));
  const wd = WEEKDAY_SHORT[utc.getUTCDay()];
  return `${wd} ${hh}:${mm}`;
}

const weekdays = [
  { v: 0, l: "Mon" },
  { v: 1, l: "Tue" },
  { v: 2, l: "Wed" },
  { v: 3, l: "Thu" },
  { v: 4, l: "Fri" },
  { v: 5, l: "Sat" },
  { v: 6, l: "Sun" },
];

const editor = ref<Recipe>({
  id: "",
  name: "New recipe",
  enabled: true,
  days_of_week: [0, 1, 2, 3, 4, 5, 6],
  times_local: ["12:00"],
  rtsp_url: null,
  filename_prefix: "",
  actions: {
    camera_tilt_pitch_deg: 0,
    center_camera_tilt: false,
    light_brightness_pct: null,
    take_snapshot: true,
    record_video_minutes: null,
  },
});

const timesText = computed({
  get: () => editor.value.times_local.join("\n"),
  set: (s: string) => {
    editor.value.times_local = s
      .split(/[\n,]+/)
      .map((t) => t.trim())
      .filter(Boolean);
  },
});

function setMsg(text: string, err = false) {
  msg.text = text;
  msg.err = err;
}

function formatErr(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const url = e.config?.url ?? "";
    const base = e.config?.baseURL ?? "";
    const st = e.response?.status;
    const data = e.response?.data as { detail?: unknown } | undefined;
    let detailText = "";
    if (data && data.detail !== undefined) {
      if (Array.isArray(data.detail)) {
        // FastAPI 422: [{loc:["body","times_local",0], msg:"...", type:"..."}]
        detailText = data.detail
          .map((d: { loc?: unknown[]; msg?: string }) => {
            const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
            return loc ? `${loc}: ${d.msg ?? ""}` : (d.msg ?? "");
          })
          .join("; ");
      } else if (typeof data.detail === "string") {
        detailText = data.detail;
      } else {
        detailText = JSON.stringify(data.detail);
      }
    }
    const tail = detailText ? ` — ${detailText}` : `: ${e.message}`;
    return `HTTP ${st} ${base}${url}${tail}`;
  }
  return String(e);
}

async function refresh() {
  const labels = ["status", "recipes", "settings"] as const;
  const settled = await Promise.allSettled([api.getStatus(), api.listRecipes(), api.getSettings()]);
  const failed: string[] = [];
  settled.forEach((r, i) => {
    if (r.status === "fulfilled") {
      if (i === 0) status.value = r.value;
      if (i === 1) recipes.value = r.value;
      if (i === 2) settings.value = r.value;
    } else {
      failed.push(`${labels[i]}: ${formatErr(r.reason)}`);
    }
  });
  if (failed.length) {
    setMsg(failed.join("\n"), true);
  } else {
    setMsg("");
  }
}

onMounted(() => {
  void refresh();
  setInterval(() => void refresh(), 5000);
  setInterval(() => {
    nowMs.value = Date.now();
  }, 1000);
});

async function saveSettingsForm() {
  if (!settings.value) return;
  busy.value = true;
  try {
    settings.value = await api.saveSettings(settings.value);
    setMsg("Settings saved.");
  } catch (e: unknown) {
    setMsg(formatErr(e), true);
  } finally {
    busy.value = false;
  }
}

async function probe() {
  if (!settings.value) return;
  busy.value = true;
  try {
    const r = await api.probeRtsp(settings.value.default_rtsp_url);
    setMsg(JSON.stringify(r, null, 2), !r.ok);
  } catch (e: unknown) {
    setMsg(formatErr(e), true);
  } finally {
    busy.value = false;
  }
}

function newRecipe() {
  editor.value = {
    id: "",
    name: "New recipe",
    enabled: true,
    days_of_week: [0, 1, 2, 3, 4, 5, 6],
    times_local: ["08:00"],
    rtsp_url: null,
    filename_prefix: "",
    actions: {
      camera_tilt_pitch_deg: 0,
      center_camera_tilt: false,
      light_brightness_pct: 50,
      take_snapshot: true,
      record_video_minutes: 1,
    },
  };
}

const tiltEnabled = computed(() => editor.value.actions.camera_tilt_pitch_deg !== null);

function onTiltToggle(enabled: boolean) {
  if (enabled) {
    if (editor.value.actions.camera_tilt_pitch_deg === null) {
      editor.value.actions.camera_tilt_pitch_deg = 0;
    }
    editor.value.actions.center_camera_tilt = false;
  } else {
    editor.value.actions.camera_tilt_pitch_deg = null;
    editor.value.actions.center_camera_tilt = false;
  }
}

function editRecipe(r: Recipe) {
  editor.value = JSON.parse(JSON.stringify(r)) as Recipe;
}

async function saveRecipe() {
  busy.value = true;
  try {
    const saved = await api.saveRecipe(editor.value);
    editor.value = saved;
    await refresh();
    setMsg("Recipe saved.");
  } catch (e: unknown) {
    setMsg(formatErr(e), true);
  } finally {
    busy.value = false;
  }
}

async function removeRecipe() {
  if (!editor.value.id) return;
  if (!confirm("Delete this recipe?")) return;
  busy.value = true;
  try {
    await api.deleteRecipe(editor.value.id);
    newRecipe();
    await refresh();
    setMsg("Recipe deleted.");
  } catch (e: unknown) {
    setMsg(formatErr(e), true);
  } finally {
    busy.value = false;
  }
}

async function runManual(fn: () => Promise<{ data: unknown }>, label: string) {
  busy.value = true;
  try {
    const r = await fn();
    setMsg(`${label}: ${JSON.stringify(r.data ?? r)}`);
  } catch (e: unknown) {
    setMsg(formatErr(e), true);
  } finally {
    busy.value = false;
  }
}

const lightTest = ref(50);
const tiltPitchDeg = ref(0);
const recordSec = ref(15);

const capturesIframeUrl = computed(() => {
  if (typeof window === "undefined") return "";
  return `${window.location.origin}/file-browser/files/extensions/timelapse-controller/captures/`;
});

const deviceTimeLabel = computed(() => {
  const dt = status.value?.device_time;
  if (!dt) return "Device time: …";
  const tzShort = dt.tz ? ` ${dt.tz}` : "";
  const tzName = dt.tz_name ? ` (${dt.tz_name})` : "";
  return `Device time: ${dt.weekday} ${dt.date} ${dt.hm}${tzShort}${tzName}`;
});

const isRunning = computed(() => status.value?.scheduler?.state === "running");

const activeStartedIso = computed(() => {
  const s = status.value?.scheduler;
  if (!s) return null;
  return s.current_action_started_at_iso || s.last_run_at_iso || null;
});

const activeElapsed = computed(() => {
  const iso = activeStartedIso.value;
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const sec = Math.max(0, Math.floor((nowMs.value - t) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m${s.toString().padStart(2, "0")}s`;
});

const activeActionLabel = computed(() =>
  actionLabel(status.value?.scheduler?.current_action ?? null) || "Running",
);

type NextRunEntry = {
  recipeId: string;
  recipeName: string;
  iso: string;
  ms: number;
};

const nextScheduled = computed<NextRunEntry | null>(() => {
  const rs = status.value?.recipes_state;
  if (!rs) return null;
  const byId = new Map(recipes.value.map((r) => [r.id, r]));
  const candidates: NextRunEntry[] = [];
  for (const [rid, info] of Object.entries(rs)) {
    if (!info?.next_run_iso) continue;
    const r = byId.get(rid);
    if (!r || !r.enabled) continue;
    const ms = Date.parse(info.next_run_iso);
    if (Number.isNaN(ms)) continue;
    candidates.push({ recipeId: rid, recipeName: r.name, iso: info.next_run_iso, ms });
  }
  candidates.sort((a, b) => a.ms - b.ms);
  return candidates[0] ?? null;
});

function recipeRunInfo(id: string) {
  return status.value?.recipes_state?.[id] ?? null;
}

function detectBrowserTimezone() {
  if (!settings.value) return;
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz) settings.value.timezone = tz;
  } catch {
    // ignore unsupported environments
  }
}
</script>

<template>
  <div class="container">
    <h1>Timelapse Controller</h1>
    <p class="small">BlueOS extension — scheduled RTSP capture, MAVLink tilt center, Lumen PWM.</p>
    <div class="device-time" :title="status?.device_time?.iso ?? ''">{{ deviceTimeLabel }}</div>

    <div class="tabs">
      <button :class="{ active: tab === 'status' }" type="button" @click="tab = 'status'">Status</button>
      <button :class="{ active: tab === 'recipes' }" type="button" @click="tab = 'recipes'">Recipes</button>
      <button :class="{ active: tab === 'settings' }" type="button" @click="tab = 'settings'">Settings</button>
      <button :class="{ active: tab === 'manual' }" type="button" @click="tab = 'manual'">Manual</button>
      <button class="secondary" type="button" :disabled="busy" @click="refresh()">Refresh</button>
    </div>

    <div v-if="msg.text" class="msg" :class="{ err: msg.err, ok: !msg.err }">{{ msg.text }}</div>

    <div v-if="tab === 'status'" class="card">
      <div v-if="isRunning" class="active-banner">
        <span class="active-badge">● {{ activeActionLabel }}…</span>
        <span class="active-name">{{ status?.scheduler?.current_recipe_name || "(recipe)" }}</span>
        <span class="active-elapsed small">{{ activeElapsed ? `elapsed ${activeElapsed}` : "" }}</span>
      </div>
      <div v-else-if="nextScheduled" class="next-line small">
        Next scheduled: <strong>{{ nextScheduled.recipeName }}</strong>
        — {{ formatLocalShort(nextScheduled.iso) }} ({{ formatRelative(nextScheduled.iso, nowMs) }})
      </div>

      <h2>Scheduler</h2>
      <pre v-if="status" class="small" style="margin: 0; overflow: auto">{{ JSON.stringify(status.scheduler, null, 2) }}</pre>
      <h2>MAVLink</h2>
      <pre v-if="status" class="small" style="margin: 0; overflow: auto">{{ JSON.stringify(status.mavlink, null, 2) }}</pre>
      <h2>Captures</h2>
      <p class="small" style="margin: 0.25rem 0 0.5rem">
        Files under <code>/usr/blueos/extensions/timelapse-controller/captures</code> via the BlueOS File Browser.
      </p>
      <iframe
        :src="capturesIframeUrl"
        title="BlueOS File Browser — captures"
        style="width: 100%; height: 560px; border: 1px solid #ccc; border-radius: 4px; background: white"
        loading="lazy"
      />
    </div>

    <div v-if="tab === 'settings'" class="card">
      <template v-if="settings">
        <label>Default RTSP URL</label>
        <input v-model="settings.default_rtsp_url" type="text" placeholder="rtsp://..." />

        <label>MAVLink connection</label>
        <input v-model="settings.mavlink_connection" type="text" />

        <div class="row">
          <div>
            <label>Light servo channel</label>
            <input v-model.number="settings.light_servo_channel" type="number" min="1" max="32" />
          </div>
          <div>
            <label>Light PWM min</label>
            <input v-model.number="settings.light_pwm_min" type="number" />
          </div>
          <div>
            <label>Light PWM max</label>
            <input v-model.number="settings.light_pwm_max" type="number" />
          </div>
        </div>

        <div class="row">
          <div>
            <label>Tilt pitch min (deg)</label>
            <input v-model.number="settings.tilt_pitch_min_deg" type="number" />
          </div>
          <div>
            <label>Tilt pitch max (deg)</label>
            <input v-model.number="settings.tilt_pitch_max_deg" type="number" />
          </div>
        </div>

        <div class="row">
          <div>
            <label>GStreamer rtspsrc latency (ms)</label>
            <input v-model.number="settings.gstreamer_latency_ms" type="number" />
          </div>
          <label class="row" style="align-items: center; margin-top: 1.4rem">
            <input v-model="settings.use_tcp_rtsp" type="checkbox" style="width: auto" />
            RTSP over TCP
          </label>
        </div>

        <label>Timezone (IANA, e.g. <code>Pacific/Honolulu</code>)</label>
        <div class="row">
          <input
            v-model="settings.timezone"
            type="text"
            placeholder="Pacific/Honolulu"
            style="flex: 1"
          />
          <button class="btn secondary" type="button" @click="detectBrowserTimezone">
            Detect from browser
          </button>
        </div>
        <p class="small" style="margin: 0.25rem 0 0">
          Empty = use the container's system time (which on BlueOS reflects the host clock if
          <code>/etc/localtime</code> is bind-mounted).
        </p>

        <label class="row" style="align-items: center; margin-top: 0.75rem">
          <input v-model="settings.restore_state_after_recipe" type="checkbox" style="width: auto" />
          Restore prior camera tilt and light after each recipe
        </label>
        <p class="small" style="margin: 0.1rem 0 0">
          When enabled, the extension reads the current gimbal pitch and light PWM before running a
          recipe and returns them to that state when the recipe finishes (success or failure). Disable
          to leave whatever the recipe last set.
        </p>

        <div class="row" style="margin-top: 0.75rem">
          <button class="btn" type="button" :disabled="busy" @click="saveSettingsForm">Save settings</button>
          <button class="btn secondary" type="button" :disabled="busy" @click="probe">Probe RTSP</button>
        </div>
      </template>
    </div>

    <div v-if="tab === 'recipes'" class="card">
      <div class="row" style="margin-bottom: 0.75rem">
        <button class="btn secondary" type="button" @click="newRecipe">New recipe</button>
      </div>
      <table v-if="recipes.length">
        <thead>
          <tr>
            <th>Name</th>
            <th>Enabled</th>
            <th>Times</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in recipes" :key="r.id">
            <td>{{ r.name }}</td>
            <td>{{ r.enabled }}</td>
            <td>{{ r.times_local.join(", ") }}</td>
            <td>
              <template v-if="recipeRunInfo(r.id)?.is_running">
                <span class="badge badge-running">
                  ● Running{{ recipeRunInfo(r.id)?.current_action ? `: ${actionLabel(recipeRunInfo(r.id)!.current_action)}` : "" }}
                </span>
              </template>
              <template v-else-if="r.enabled && recipeRunInfo(r.id)?.next_run_iso">
                <span class="badge badge-next">
                  Next: {{ formatLocalShort(recipeRunInfo(r.id)!.next_run_iso) }}
                  ({{ formatRelative(recipeRunInfo(r.id)!.next_run_iso, nowMs) }})
                </span>
              </template>
            </td>
            <td><button class="btn secondary" type="button" @click="editRecipe(r)">Edit</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else class="small">No recipes yet.</p>

      <h2>Editor</h2>
      <label>Name</label>
      <input v-model="editor.name" type="text" />
      <label class="row" style="margin-top: 0.5rem">
        <input v-model="editor.enabled" type="checkbox" style="width: auto" />
        Enabled
      </label>

      <label>Days (local)</label>
      <div class="row">
        <label v-for="d in weekdays" :key="d.v" class="row" style="margin: 0">
          <input v-model="editor.days_of_week" type="checkbox" style="width: auto" :value="d.v" />
          {{ d.l }}
        </label>
      </div>

      <label>Times (one HH:MM per line, 24-hour clock)</label>
      <p class="small" style="margin: 0.1rem 0 0.25rem">
        24-hour format — e.g. midnight = <code>00:00</code>, 1 PM = <code>13:00</code>, 8:30 PM = <code>20:30</code>. One time
        per line or comma-separated. Times run in the device's local timezone shown at the top of the page.
      </p>
      <textarea v-model="timesText" />

      <label>RTSP override (optional)</label>
      <input
        :value="editor.rtsp_url ?? ''"
        type="text"
        placeholder="leave empty to use default from settings"
        @input="editor.rtsp_url = ($event.target as HTMLInputElement).value.trim() || null"
      />

      <label>Filename prefix</label>
      <input v-model="editor.filename_prefix" type="text" />

      <h2>Actions</h2>
      <label class="row">
        <input
          :checked="tiltEnabled"
          type="checkbox"
          style="width: auto"
          @change="onTiltToggle(($event.target as HTMLInputElement).checked)"
        />
        Set camera tilt
      </label>
      <div v-if="tiltEnabled" class="row" style="align-items: center">
        <input
          v-model.number="editor.actions.camera_tilt_pitch_deg"
          type="number"
          step="1"
          min="-90"
          max="90"
          style="max-width: 6rem"
        />
        <span class="small">°</span>
        <button
          class="btn secondary"
          type="button"
          style="padding: 0.25rem 0.5rem; font-size: 0.85rem"
          @click="editor.actions.camera_tilt_pitch_deg = 0"
        >
          Center (0°)
        </button>
        <button
          class="btn secondary"
          type="button"
          style="padding: 0.25rem 0.5rem; font-size: 0.85rem"
          @click="editor.actions.camera_tilt_pitch_deg = -45"
        >
          Down 45°
        </button>
      </div>
      <p v-if="tiltEnabled" class="small" style="margin: 0.1rem 0 0.25rem">
        0° = center. Allowed range −90°…+90° (Settings caps actual sent commands to your tilt min/max).
      </p>
      <label class="row">
        <input v-model="editor.actions.take_snapshot" type="checkbox" style="width: auto" />
        Take snapshot (ffmpeg)
      </label>
      <label>Light brightness % (empty = unchanged)</label>
      <input
        :value="editor.actions.light_brightness_pct ?? ''"
        type="number"
        min="0"
        max="100"
        placeholder="unchanged"
        @input="
          (e) =>
            (editor.actions.light_brightness_pct =
              (e.target as HTMLInputElement).value === '' ? null : Number((e.target as HTMLInputElement).value))
        "
      />
      <label>Record video (minutes, empty = off)</label>
      <input
        :value="editor.actions.record_video_minutes ?? ''"
        type="number"
        step="0.1"
        min="0"
        placeholder="off"
        @input="
          (e) =>
            (editor.actions.record_video_minutes =
              (e.target as HTMLInputElement).value === '' ? null : Number((e.target as HTMLInputElement).value))
        "
      />

      <div class="row" style="margin-top: 0.75rem">
        <button class="btn" type="button" :disabled="busy" @click="saveRecipe">Save recipe</button>
        <button class="btn secondary" type="button" :disabled="busy || !editor.id" @click="removeRecipe">Delete</button>
      </div>
    </div>

    <div v-if="tab === 'manual'" class="card">
      <p class="small">
        MAVLink uses Settings → connection string and light servo channel. Tilt uses
        <code>MAV_CMD_DO_GIMBAL_MANAGER_TILTPAN</code> (deg, 0 = center; range from Settings, default −70…+70). Light manual
        uses <code>MAV_CMD_DO_SET_SERVO</code>: 0% = 1100 µs (off), 100% = 1900 µs (full).
      </p>
      <h2>Tilt</h2>
      <div class="row">
        <button class="btn" type="button" :disabled="busy" @click="runManual(() => api.manualTiltCenter(), 'tilt-center')">
          Center (0°)
        </button>
      </div>
      <div class="row">
        <label style="margin: 0; min-width: 7rem">Pitch (deg)</label>
        <input v-model.number="tiltPitchDeg" type="number" min="-70" max="70" step="1" style="max-width: 6rem" />
        <button class="btn" type="button" :disabled="busy" @click="runManual(() => api.manualTiltPitch(tiltPitchDeg), 'tilt')">
          Set tilt
        </button>
      </div>
      <h2>Light</h2>
      <p class="small">Channel from Settings (default 13). Brightness 0–100 maps linearly 1100–1900 µs.</p>
      <div class="row">
        <input v-model.number="lightTest" type="number" min="0" max="100" style="max-width: 6rem" />
        <button class="btn" type="button" :disabled="busy" @click="runManual(() => api.manualLight(lightTest), 'light')">
          Set light %
        </button>
        <button class="btn secondary" type="button" :disabled="busy" @click="runManual(() => api.manualLight(0), 'light-off')">
          Off (0%)
        </button>
        <button class="btn secondary" type="button" :disabled="busy" @click="runManual(() => api.manualLight(100), 'light-full')">
          Full (100%)
        </button>
      </div>
      <div class="row">
        <button class="btn" type="button" :disabled="busy" @click="runManual(() => api.manualSnapshot(), 'snapshot')">
          Snapshot
        </button>
      </div>
      <div class="row">
        <input v-model.number="recordSec" type="number" min="1" max="600" style="max-width: 6rem" />
        <button
          class="btn"
          type="button"
          :disabled="busy"
          @click="runManual(() => api.manualRecord(recordSec), 'record')"
        >
          Record (seconds)
        </button>
      </div>
    </div>
  </div>
</template>
