import axios from "axios";

/**
 * BlueOS may serve this app under a subpath (e.g. /extensionv2/timelapsecontroller/).
 * Axios URLs that start with "/" hit the site root, not the extension — use a resolved
 * base URL and paths without a leading slash.
 */
function apiBaseURL(): string {
  const raw = (import.meta.env.BASE_URL || "/") as string;
  if (typeof window === "undefined") {
    return raw.endsWith("/") ? raw : `${raw}/`;
  }
  try {
    const resolved = new URL(raw, window.location.href);
    let href = resolved.href;
    if (!href.endsWith("/")) {
      href += "/";
    }
    return href;
  } catch {
    return "/";
  }
}

const client = axios.create({ baseURL: apiBaseURL() });

export type SchedulerState = {
  state: string;
  message: string;
  current_recipe_id: string | null;
  current_recipe_name: string | null;
  last_run_at_iso: string | null;
  next_wake_iso: string | null;
};

export type Settings = {
  default_rtsp_url: string;
  mavlink_connection: string;
  light_servo_channel: number;
  light_pwm_min: number;
  light_pwm_max: number;
  tilt_pitch_min_deg: number;
  tilt_pitch_max_deg: number;
  gstreamer_latency_ms: number;
  use_tcp_rtsp: boolean;
};

export type RecipeActions = {
  center_camera_tilt: boolean;
  light_brightness_pct: number | null;
  take_snapshot: boolean;
  record_video_minutes: number | null;
};

export type Recipe = {
  id: string;
  name: string;
  enabled: boolean;
  days_of_week: number[];
  times_local: string[];
  rtsp_url: string | null;
  filename_prefix: string;
  actions: RecipeActions;
};

export type DeviceTime = {
  iso: string;
  hm: string;
  date: string;
  weekday: string;
  weekday_index: number;
  tz: string;
};

export async function getStatus() {
  const { data } = await client.get("api/v1/status");
  return data as {
    scheduler: SchedulerState;
    mavlink: Record<string, unknown>;
    device_time?: DeviceTime;
    settings_summary: Record<string, unknown>;
  };
}

export async function getSettings() {
  const { data } = await client.get<Settings>("api/v1/settings");
  return data;
}

export async function saveSettings(s: Settings) {
  const { data } = await client.put<Settings>("api/v1/settings", s);
  return data;
}

export async function listRecipes() {
  const { data } = await client.get<Recipe[]>("api/v1/recipes");
  return data;
}

export async function saveRecipe(r: Recipe) {
  if (r.id) {
    const { data } = await client.put<Recipe>(`api/v1/recipes/${encodeURIComponent(r.id)}`, r);
    return data;
  }
  const { data } = await client.post<Recipe>("api/v1/recipes", r);
  return data;
}

export async function deleteRecipe(id: string) {
  await client.delete(`api/v1/recipes/${encodeURIComponent(id)}`);
}

export async function probeRtsp(url: string) {
  const { data } = await client.post("api/v1/probe-rtsp", { url });
  return data;
}

export async function manualTiltCenter() {
  return client.post("api/v1/manual/tilt-center");
}

export async function manualTiltPitch(pitchDeg: number) {
  return client.post("api/v1/manual/tilt", { pitch_deg: pitchDeg });
}

export async function manualLight(brightness_pct: number) {
  return client.post("api/v1/manual/light", { brightness_pct });
}

export async function manualSnapshot() {
  return client.post("api/v1/manual/snapshot");
}

export async function manualRecord(seconds: number) {
  return client.post("api/v1/manual/record", { seconds });
}
