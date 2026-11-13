// With Vistazo installed, this add-on's place is inside Vistazo's settings: its own entry in
// Decky's list of plugins is put away once (Decky's own "hidden plugins", which the player can
// undo in Decky's settings). Done one time only, so a player who brings it back keeps it; and if
// Vistazo is not there, the entry comes back, since it is then the only way in.
declare const DeckyBackend: any;
declare const DeckyPluginLoader: any;

const hidden = async (): Promise<string[]> => {
  const list = await DeckyBackend.call("utilities/settings/get", "hiddenPlugins", []);
  return Array.isArray(list) ? list.filter((x: unknown): x is string => typeof x === "string") : [];
};
const setHidden = (list: string[]) => DeckyPluginLoader.hiddenPluginsService.update(list);

export function tuckAway(name: string, key: string): () => void {
  const flag = `${key}:tucked`;
  const there = () => !!(window as any).Vistazo;
  const look = async () => {
    try {
      const was = localStorage.getItem(flag);
      if (there() && !was) {
        const list = await hidden();
        if (!list.includes(name)) await setHidden([...list, name]);
        localStorage.setItem(flag, "1");
      } else if (!there() && was === "1") {
        const list = await hidden();
        if (list.includes(name)) await setHidden(list.filter((n) => n !== name));
        localStorage.removeItem(flag);
      }
    } catch (e) {
      console.warn(`[${name}] Decky's list`, e);
    }
  };
  // A while after loading: Vistazo may load after this add-on.
  const timer = window.setTimeout(look, 20000);
  return () => window.clearTimeout(timer);
}
