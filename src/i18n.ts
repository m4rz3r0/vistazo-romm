import { safe } from "./safe";
import { scopeNames } from "./romm";
// Interface text in Spanish or English, following Steam's own interface language
// (Spanish for any Spanish locale, English for everything else).
declare const LocalizationManager: any;

const steamLanguage = safe(() => String(LocalizationManager.m_rgLocalesToUse[0] ?? ""), "");
export const lang: "es" | "en" =
  /^es|^latam|spanish/i.test(steamLanguage || navigator.language) ? "es" : "en";
export const locale = lang === "es" ? "es-ES" : "en-US";

export const num = (n: number, digits = 0) =>
  n.toLocaleString(locale, { maximumFractionDigits: digits });

const es = {
  rommLabel: "Logros al terminar de jugar",
  rommUnlinked: "Para verlos nada más cerrar un juego retro, Vistazo pide a RomM que los vuelva a leer. Necesita su propia clave de RomM.",
  rommLinked: "Conectado: al cerrar un juego retro, sus logros se actualizan.",
  rommMoved: "La dirección de RomM ha cambiado: vuelve a conectar.",
  serverLabel: "Dirección de RomM",
  serverNone: "Sin dirección todavía.",
  serverSet: "Poner dirección",
  serverChange: "Cambiar",
  serverSteps: "La dirección con la que abres RomM en el navegador, por ejemplo https://romm.example.com.",
  serverField: "Dirección",
  serverKeep: "Guardar",
  serverAsking: "Comprobando que RomM responde ahí…",
  serverInsecure: "Aceptar su certificado aunque no se pueda comprobar",
  serverInsecureDesc: "Solo si es tu propio servidor con un certificado hecho por ti.",
  serverAnswer: (a: string): string =>
    a === "ok" ? "RomM responde ahí. Guardada."
    : a === "unreachable" ? "Guardada, pero ahora no responde. Si RomM está apagado, irá cuando se encienda."
    : a === "certificate" ? "Su certificado no se puede comprobar. No se ha guardado."
    : a === "not-romm" ? "Ahí responde algo que no es RomM. No se ha guardado."
    : a === "invalid" ? "No es una dirección: empieza por https:// o http://."
    : "No se pudo guardar.",
  rommPair: "Conectar con RomM",
  rommUnlink: "Desconectar",
  rommPairSteps:
    "En RomM, en Ajustes › Client API Tokens, crea una clave con solo el permiso Me (read y write) y pulsa «Emparejar dispositivo». Escribe aquí el código que aparece: caduca en un minuto.",
  rommCode: "Código",
  rommAsking: "Preguntando a RomM…",
  rommDone: "Hecho",
  rommAsleep: "Conectado, pero RomM no responde ahora mismo.",
  rommDropped: "RomM ya no acepta la clave que había: vuelve a conectar.",
  rommAnswer: (a: { result: string; missing: string[]; extra: string[] }, games: boolean) => {
    switch (a.result) {
      case "ok":
        return games ? "Conectado: juegos y logros." : "Conectado: los logros se actualizarán al cerrar un juego.";
      case "limited":
        return `Conectado, pero solo para los logros. Para llevar los juegos a la clave le falta: ${scopeNames(a.missing)}. Crea otra con todo y vuelve a conectar.`;
      case "code":
        return "El código tiene 8 letras y números.";
      case "invalid":
        return "RomM no reconoce ese código: es incorrecto o ha caducado (dura un minuto). Pide otro.";
      case "scopes":
        return a.extra.length
          ? `Esa clave puede hacer más de lo que Vistazo debe tener: ${scopeNames(a.extra)}. Se ha borrado en RomM; crea otra sin eso.`
          : `A esa clave le falta: ${scopeNames(a.missing)}. Se ha borrado en RomM; crea otra.`;
      case "no-server":
        return "Falta la dirección de RomM.";
      case "unreachable":
        return "RomM no responde. Si estaba dormido, tarda un poco en arrancar: pide otro código y prueba de nuevo.";
      case "busy":
        return "RomM pide esperar un momento antes de probar otro código.";
      default:
        return "No se pudo conectar. El código ya no vale: pide otro.";
    }
  },
  section: "RomM",
  needsVistazo: "Este complemento necesita Vistazo: es quien muestra los juegos.",
  savesLabel: "Partidas guardadas",
  savesDesc: "Al cerrar un juego de DS o de Wii, su partida sube a RomM; si RomM tiene una más nueva y la de la Deck no ha cambiado, se trae, guardando antes una copia. Si cambiaron las dos, no se toca nada hasta que elijas.",
  saveUseRomm: "Usar la partida de RomM",
  saveUseDeck: "Usar la de esta Deck",
  biosGet: "Descargar BIOS",
  noteSent: "Partida subida a RomM.",
  noteBrought: "Traída la partida más nueva de RomM. La anterior queda copiada en la Deck.",
  noteConflict: "La partida cambió aquí y en RomM. Elige cuál usar en su ficha.",
  noteUnreachable: "RomM no responde. Se intentará de nuevo al abrir su ficha.",
  noteRefused: "RomM no acepta la clave. Vuelve a conectar en los ajustes.",
  noteLayout: "La partida no está donde se espera. No se ha tocado nada.",
  noteSaveFailed: "No se pudo sincronizar la partida. No se ha tocado nada.",
  noteBiosOk: "BIOS completas.",
  noteBiosFailed: "No se pudieron traer todas las BIOS.",
  uninstall: "Desinstalar",
  tenderLabel: "Entradas de Tender",
  tenderOn: (n: number) => `Tender tiene ${n} juegos en Steam. Desactívalo o desinstálalo en Decky y podrás quedártelos aquí, con su tiempo de juego, imágenes y colecciones.`,
  tenderOnMine: "Tender vuelve a estar activo y no deja arrancar sus entradas desde aquí. Devuélveselas, o desactívalo otra vez.",
  tenderOff: (n: number) => `Tender está apagado. Quédate con sus ${n} juegos: conservan tiempo de juego, imágenes y colecciones, y se quitan sus duplicados de aquí.`,
  tenderAllMine: (n: number) => `Los ${n} juegos de Tender son ya de este complemento.`,
  tenderTitle: "Quedarse con los juegos de Tender",
  tenderConfirm: (n: number) => `Las ${n} entradas de Tender pasarán a arrancarse desde aquí, y los juegos que bajó Tender se podrán desinstalar desde su ficha. Las entradas repetidas de este complemento se quitan; ningún juego se borra. Se puede deshacer.`,
  tenderTake: "Quedármelos",
  tenderStep: (n: number, of: number) => `Pasando ${n} de ${of}…`,
  tenderTaken: "Hecho. Ya puedes desinstalar Tender; no uses su opción de quitar todos los juegos.",
  tenderGive: "Devolvérselos a Tender",
  tenderGiven: "Devueltos. Vuelve a activar Tender para usarlos.",
  libraryLabel: "Biblioteca de RomM",
  libraryDesc: "Pone en Steam todos los juegos de RomM, en una colección por consola. Cada juego se instala después desde su ficha.",
  libraryTender: "Pone en Steam todos los juegos de RomM, en una colección por consola. Tender ya tiene los suyos: mientras siga instalado, cada juego saldrá dos veces.",
  libraryBring: "Traer la biblioteca",
  libraryStep: (n: number, of: number) => `Añadiendo juegos: ${n} de ${of}`,
  libraryDone: "Biblioteca al día.",
  libraryDrop: "Quitar los no instalados",
  libraryDropped: "Quedan solo los juegos instalados.",
  emulatorLabel: "Emulador",
  emulatorDefault: "El que elija RetroDECK",
  emulatorDesc: "Con qué emulador arranca este juego.",
  emulatorNoSaves: (name: string) => `Con este, las partidas no se sincronizan con RomM: solo con ${name}.`,
  languageLabel: "Idioma de los juegos",
  languageDesc: "Qué versión de cada juego usan las entradas nuevas, cuando RomM tiene varias.",
  languageMain: "La principal de RomM",
  versionLabel: "Versión",
  versionDesc: "La partida pasa a la versión nueva si esta no tiene una; la de la otra versión se queda guardada.",
  versionBringing: (n: number) => `Trayendo la otra versión: ${n} %. Mientras, se juega la de antes.`,
  versionDone: "Versión cambiada.",
  versionWaiting: "La otra versión ya está aquí: se cambia al cerrar el juego, con la partida de hoy.",
  versionNoAchievements: "Esta versión no tiene logros en RetroAchievements.",
  collectionsLabel: "Colecciones de RomM",
  collectionsDesc: "Elige qué colecciones de RomM ver también en Steam.",
  collectionsOn: (n: number) => (n === 1 ? "1 colección de RomM se ve en Steam." : `${n} colecciones de RomM se ven en Steam.`),
  collectionsChoose: "Elegir",
  collectionsSteps: "Cada colección que enciendas aparece en Steam como «RomM: [nombre]» y trae los juegos que falten. Al apagarla se quita solo la colección; los juegos se quedan.",
  collectionsLoading: "Preguntando a RomM…",
  collectionsNone: "RomM no tiene colecciones todavía.",
  collectionsGames: (n: number) => (n === 1 ? "1 juego" : `${n} juegos`),
  collectionsDone: (name: string) => `«${name}» está al día en Steam.`,
  collectionsOff: (name: string) => `«${name}» ya no se ve en Steam.`,
  languageName: (l: string) =>
    (({ Spanish: "Español", English: "Inglés", French: "Francés", German: "Alemán", Italian: "Italiano", Japanese: "Japonés", Portuguese: "Portugués", Dutch: "Neerlandés", Korean: "Coreano", Chinese: "Chino" }) as Record<string, string>)[l] ?? l,
  regionName: (r: string) =>
    (({ Spain: "España", Europe: "Europa", USA: "EE. UU.", Japan: "Japón", World: "Mundo", France: "Francia", Germany: "Alemania", Italy: "Italia" }) as Record<string, string>)[r] ?? r,
  collectionsKind: (kind: string) =>
    (({
      standard: "Colecciones",
      smart: "Colecciones inteligentes",
      collection: "Series",
      franchise: "Franquicias",
      genre: "Géneros",
      company: "Compañías",
      mode: "Modos de juego",
    }) as Record<string, string>)[kind] ?? "Otras",
  ownGamesLabel: "Juegos con Vistazo",
  ownGamesDesc: "Vistazo descarga e inicia por sí mismo los juegos de RomM que le pases, uno a uno. En pruebas.",
  rommLabelGames: "Conexión con RomM",
  rommUnlinkedGames: "Para ver los juegos, descargarlos y actualizar sus logros, Vistazo necesita su propia clave de RomM.",
  rommLinkedGames: "Conectado: juegos y logros.",
  rommLimited: "Conectado solo para los logros. Para llevar los juegos hace falta otra clave: vuelve a conectar.",
  rommAgain: "Conectar de nuevo",
  rommPairStepsGames:
    "En RomM, en Ajustes › Client API Tokens, crea una clave con estos permisos: Assets (read y write), Collections (read), Firmware (read), Me (read y write), Platforms (read) y Roms (read). Para enviar también el tiempo de juego, añade Roms user (write). Pulsa «Emparejar dispositivo» y escribe aquí el código que aparece: caduca en un minuto.",
  ownName: (game: string) => `${game} · Vistazo`,
  ownOffer:
    "Crea en Steam una segunda entrada de este juego, a cargo de Vistazo: la descarga de RomM y la inicia. La de Tender sigue igual. Las dos usan la misma partida guardada, y Tender no sincroniza lo que juegues desde la de Vistazo.",
  ownAdd: "Pasarlo a Vistazo",
  ownRemove: "Quitar de Vistazo",
  ownDownload: "Descargar de nuevo",
  ownLost: (n: number) => (n === 1 ? "Un juego ya no está en Steam" : `${n} juegos ya no están en Steam`),
  ownLostDesc: "Su entrada se quitó desde Steam. Vistazo aún guarda sus datos y los archivos que descargó.",
  ownLostClear: "Limpiar",
  ownGone: (n: number) => (n === 1 ? "Un juego ya no está en RomM" : `${n} juegos ya no están en RomM`),
  ownGoneDesc: "Se borraron en RomM. Quitar sus entradas borra solo los archivos que trajo Vistazo; las partidas se quedan.",
  ownGoneClear: "Quitar sus entradas",
  ownGoneAsk: "¿Quitar estas entradas de Steam?",
  ownGoneAskDesc: (names: string) => `${names}. Su tiempo de juego en Steam se pierde con la entrada; las partidas guardadas se quedan.`,
  ownQueued: "En cola: empieza cuando acabe la descarga en marcha.",
  ownPaused: (p: number) => `En pausa: ${p} %`,
  ownPause: "Pausar la descarga",
  ownResume: "Seguir descargando",
  ownDiscard: "Descartar lo descargado",
  playLimited: "El tiempo de juego no llega a RomM. Para enviarlo, conecta de nuevo con una clave que además tenga Roms user (write).",
  ownDownloading: (p: number) => `Descargando: ${p} %`,
  ownReady: "Listo: está en la estantería Retro, con un ojo en la esquina, y en tu biblioteca.",
  ownNoFile: "La entrada está creada, pero falta el juego.",
  ownWhy: (why: string) =>
    (({
      unreachable: "RomM no responde o la descarga se cortó. Prueba otra vez.",
      refused: "RomM ha rechazado la clave: vuelve a conectar.",
      unlinked: "Falta conectar con RomM.",
      limited: "La conexión con RomM solo sirve para los logros: vuelve a conectar.",
      moved: "La dirección de RomM ha cambiado: vuelve a conectar.",
      space: "No hay sitio en el disco.",
      exists: "Ya hay otro archivo con ese nombre en la carpeta del juego. No se ha tocado.",
      system: "Vistazo aún no sabe dónde guarda RetroDECK los juegos de esta consola.",
      retrodeck: "No se encuentra la carpeta de juegos de RetroDECK.",
      archive: "El juego llegó en un archivo que no se pudo abrir.",
      damaged: "Lo descargado no coincide con el juego que tiene RomM. Prueba otra vez.",
      disk: "No se pudo escribir en el disco.",
      busy: "Hay una descarga en marcha.",
      running: "El juego está en marcha: ciérralo antes.",
      steam: "Steam no dejó crear la entrada.",
      game: "RomM no tiene ese juego.",
      "tender-on": "Tender está activo: desactívalo antes en Decky.",
      "not-tender": "Esa entrada ya no es de Tender.",
      collection: "Esa colección no se pudo guardar.",
    }) as Record<string, string>)[why] ?? "No se pudo completar.",
};

const en: typeof es = {
  rommLabel: "Achievements after playing",
  rommUnlinked: "To see them as soon as a retro game closes, Vistazo asks RomM to read them again. It needs its own RomM key.",
  rommLinked: "Connected: when a retro game closes, its achievements are updated.",
  rommMoved: "RomM's address changed: connect again.",
  serverLabel: "RomM address",
  serverNone: "No address yet.",
  serverSet: "Set address",
  serverChange: "Change",
  serverSteps: "The address you open RomM with in a browser, for example https://romm.example.com.",
  serverField: "Address",
  serverKeep: "Save",
  serverAsking: "Checking that RomM answers there…",
  serverInsecure: "Accept its certificate even if it cannot be checked",
  serverInsecureDesc: "Only for your own server with a certificate you made yourself.",
  serverAnswer: (a: string): string =>
    a === "ok" ? "RomM answers there. Saved."
    : a === "unreachable" ? "Saved, but it is not answering now. If RomM is off, it will work once it is on."
    : a === "certificate" ? "Its certificate cannot be checked. Not saved."
    : a === "not-romm" ? "Something that is not RomM answers there. Not saved."
    : a === "invalid" ? "Not an address: it starts with https:// or http://."
    : "It could not be saved.",
  rommPair: "Connect to RomM",
  rommUnlink: "Disconnect",
  rommPairSteps:
    "In RomM, under Settings › Client API Tokens, create a token with only the Me permission (read and write) and press “Pair device”. Type the code it shows here: it expires in a minute.",
  rommCode: "Code",
  rommAsking: "Asking RomM…",
  rommDone: "Done",
  rommAsleep: "Connected, but RomM is not answering right now.",
  rommDropped: "RomM no longer accepts the key that was here: connect again.",
  rommAnswer: (a: { result: string; missing: string[]; extra: string[] }, games: boolean) => {
    switch (a.result) {
      case "ok":
        return games ? "Connected: games and achievements." : "Connected: achievements will update when a game closes.";
      case "limited":
        return `Connected, but for achievements only. To run the games the key lacks: ${scopeNames(a.missing)}. Create another with everything and connect again.`;
      case "code":
        return "The code is 8 letters and numbers.";
      case "invalid":
        return "RomM does not know that code: it is wrong or has expired (it lasts a minute). Get another.";
      case "scopes":
        return a.extra.length
          ? `That key can do more than Vistazo should hold: ${scopeNames(a.extra)}. It was deleted in RomM; create another without those.`
          : `That key lacks: ${scopeNames(a.missing)}. It was deleted in RomM; create another.`;
      case "no-server":
        return "RomM's address is missing.";
      case "unreachable":
        return "RomM is not answering. If it was asleep it takes a while to start: get another code and try again.";
      case "busy":
        return "RomM asks to wait a moment before trying another code.";
      default:
        return "Could not connect. The code is used up: get another.";
    }
  },
  section: "RomM",
  needsVistazo: "This add-on needs Vistazo: it is what shows the games.",
  savesLabel: "Save games",
  savesDesc: "When a DS or Wii game closes, its save goes to RomM; if RomM has a newer one and the Deck's has not changed, it is brought, keeping a copy first. If both changed, nothing is touched until you choose.",
  saveUseRomm: "Use RomM's save",
  saveUseDeck: "Use this Deck's",
  biosGet: "Download BIOS",
  noteSent: "Save sent to RomM.",
  noteBrought: "Brought RomM's newer save. The previous one is kept as a copy on the Deck.",
  noteConflict: "The save changed here and on RomM. Choose which to keep on its page.",
  noteUnreachable: "RomM is not answering. It will be tried again when its page opens.",
  noteRefused: "RomM does not accept the key. Connect again in the settings.",
  noteLayout: "The save is not where it should be. Nothing was touched.",
  noteSaveFailed: "The save could not be synced. Nothing was touched.",
  noteBiosOk: "BIOS files complete.",
  noteBiosFailed: "Not all BIOS files could be brought.",
  uninstall: "Uninstall",
  tenderLabel: "Tender's entries",
  tenderOn: (n: number) => `Tender has ${n} games in Steam. Turn it off or uninstall it in Decky and you can keep them here, with their play time, pictures and collections.`,
  tenderOnMine: "Tender is on again and does not let its entries start from here. Give them back, or turn it off again.",
  tenderOff: (n: number) => `Tender is off. Keep its ${n} games: they keep their play time, pictures and collections, and their duplicates here are removed.`,
  tenderAllMine: (n: number) => `Tender's ${n} games already belong to this add-on.`,
  tenderTitle: "Keep Tender's games",
  tenderConfirm: (n: number) => `Tender's ${n} entries will start from here, and the games Tender downloaded can be uninstalled from their page. This add-on's duplicate entries are removed; no game is deleted. It can be undone.`,
  tenderTake: "Keep them",
  tenderStep: (n: number, of: number) => `Moving ${n} of ${of}…`,
  tenderTaken: "Done. You can uninstall Tender now; do not use its option to remove all games.",
  tenderGive: "Give them back to Tender",
  tenderGiven: "Given back. Turn Tender on again to use them.",
  libraryLabel: "RomM library",
  libraryDesc: "Puts every RomM game in Steam, in one collection per console. Each game is then installed from its own page.",
  libraryTender: "Puts every RomM game in Steam, in one collection per console. Tender already has its own: while it stays installed, each game will show twice.",
  libraryBring: "Bring the library",
  libraryStep: (n: number, of: number) => `Adding games: ${n} of ${of}`,
  libraryDone: "Library up to date.",
  libraryDrop: "Remove the ones not installed",
  libraryDropped: "Only installed games are left.",
  emulatorLabel: "Emulator",
  emulatorDefault: "Whichever RetroDECK picks",
  emulatorDesc: "Which emulator this game starts with.",
  emulatorNoSaves: (name: string) => `With this one, saves are not kept in step with RomM: only with ${name}.`,
  languageLabel: "Language of the games",
  languageDesc: "Which version of each game new entries use, when RomM has several.",
  languageMain: "RomM's main one",
  versionLabel: "Version",
  versionDesc: "The save moves to the new version if it has none; the other version's save stays kept.",
  versionBringing: (n: number) => `Bringing the other version: ${n} %. Meanwhile the old one plays.`,
  versionDone: "Version changed.",
  versionWaiting: "The other version is here: it switches when the game closes, with today's save.",
  versionNoAchievements: "This version has no RetroAchievements set.",
  collectionsLabel: "RomM collections",
  collectionsDesc: "Choose which RomM collections to see in Steam too.",
  collectionsOn: (n: number) => (n === 1 ? "1 RomM collection is shown in Steam." : `${n} RomM collections are shown in Steam.`),
  collectionsChoose: "Choose",
  collectionsSteps: "Each collection you turn on shows in Steam as “RomM: [name]” and brings the games that are missing. Turning it off removes only the collection; the games stay.",
  collectionsLoading: "Asking RomM…",
  collectionsNone: "RomM has no collections yet.",
  collectionsGames: (n: number) => (n === 1 ? "1 game" : `${n} games`),
  collectionsDone: (name: string) => `“${name}” is up to date in Steam.`,
  collectionsOff: (name: string) => `“${name}” is no longer shown in Steam.`,
  languageName: (l: string) => l,
  regionName: (r: string) => r,
  collectionsKind: (kind: string) =>
    (({
      standard: "Collections",
      smart: "Smart collections",
      collection: "Series",
      franchise: "Franchises",
      genre: "Genres",
      company: "Companies",
      mode: "Game modes",
    }) as Record<string, string>)[kind] ?? "Others",
  ownGamesLabel: "Games by Vistazo",
  ownGamesDesc: "Vistazo downloads and starts the RomM games you hand it by itself, one at a time. Experimental.",
  rommLabelGames: "RomM connection",
  rommUnlinkedGames: "To see the games, download them and update their achievements, Vistazo needs its own RomM key.",
  rommLinkedGames: "Connected: games and achievements.",
  rommLimited: "Connected for achievements only. Running the games needs another key: connect again.",
  rommAgain: "Connect again",
  rommPairStepsGames:
    "In RomM, under Settings › Client API Tokens, create a token with these permissions: Assets (read and write), Collections (read), Firmware (read), Me (read and write), Platforms (read) and Roms (read). To send play time too, add Roms user (write). Press “Pair device” and type the code it shows here: it expires in a minute.",
  ownName: (game: string) => `${game} · Vistazo`,
  ownOffer:
    "Adds a second entry of this game to Steam, run by Vistazo: it downloads it from RomM and starts it. Tender's entry stays as it is. Both use the same save, and Tender does not sync what you play from Vistazo's.",
  ownAdd: "Hand it to Vistazo",
  ownRemove: "Remove from Vistazo",
  ownDownload: "Download again",
  ownLost: (n: number) => (n === 1 ? "One game is no longer in Steam" : `${n} games are no longer in Steam`),
  ownLostDesc: "Their entry was removed in Steam. Vistazo still keeps their data and the files it downloaded.",
  ownLostClear: "Clear",
  ownGone: (n: number) => (n === 1 ? "One game is no longer in RomM" : `${n} games are no longer in RomM`),
  ownGoneDesc: "They were deleted in RomM. Removing their entries deletes only the files Vistazo brought; saves stay.",
  ownGoneClear: "Remove their entries",
  ownGoneAsk: "Remove these entries from Steam?",
  ownGoneAskDesc: (names: string) => `${names}. Their play time in Steam goes with the entry; saves stay.`,
  ownQueued: "Queued: it starts when the download under way ends.",
  ownPaused: (p: number) => `Paused: ${p}%`,
  ownPause: "Pause the download",
  ownResume: "Go on downloading",
  ownDiscard: "Throw away what was downloaded",
  playLimited: "Play time does not reach RomM. To send it, connect again with a token that also has Roms user (write).",
  ownDownloading: (p: number) => `Downloading: ${p}%`,
  ownReady: "Ready: it is on the Retro shelf, with an eye in its corner, and in your library.",
  ownNoFile: "The entry is there, but the game is missing.",
  ownWhy: (why: string) =>
    (({
      unreachable: "RomM is not answering or the download was cut short. Try again.",
      refused: "RomM refused the key: connect again.",
      unlinked: "Connect to RomM first.",
      limited: "The RomM connection is for achievements only: connect again.",
      moved: "RomM's address changed: connect again.",
      space: "There is no room on the disk.",
      exists: "Another file with that name is already in the game's folder. It was left alone.",
      system: "Vistazo does not know yet where RetroDECK keeps this console's games.",
      retrodeck: "RetroDECK's games folder was not found.",
      archive: "The game came in an archive that could not be opened.",
      damaged: "What was downloaded does not match the game RomM has. Try again.",
      disk: "The disk could not be written.",
      busy: "A download is under way.",
      running: "The game is running: close it first.",
      steam: "Steam did not let the entry be made.",
      game: "RomM does not have that game.",
      "tender-on": "Tender is on: turn it off in Decky first.",
      "not-tender": "That entry is no longer Tender's.",
      collection: "That collection could not be kept.",
    }) as Record<string, string>)[why] ?? "It could not be completed.",
};

export const t = lang === "es" ? es : en;
