---
name: creo-jlink
description: Run automation inside PTC Creo Parametric via J-Link (the Java API) — batch STEP/BOM/drawing export, mass properties. Use for Creo 自动化 / 二次开发 / 批量导出, otk.jar, protk.dat, parametric.bat, pfcSession, or when Creo starts up fine but a J-Link app silently never runs.
---

# Creo J-Link — Operation Skill

Write a Java class, have Creo load and run it at startup. Batch-export a few hundred parts to STEP, dump a BOM, generate drawing PDFs — without touching the mouse.

Bundled:

| File | What it is |
|---|---|
| `creo-run.bat` | Launcher. Checks your environment, generates the three config files Creo needs, compiles, starts Creo with the right JVM. |
| `HelloJlink.java` | Smoke test + skeleton to copy. Proves the chain works, carries the reflection helpers. |

## When this skill applies

- Batch operations across many parts/assemblies (export, measure, rename, parameter edit)
- Anything you would otherwise do by clicking the same menu 300 times
- Diagnosing why a J-Link app silently fails to run

It does **not** cover: Pro/TOOLKIT in C, Creo Object TOOLKIT C++, Windchill, or driving Creo's GUI by simulated clicks.

## Quick start

1. Make a working directory. Drop in `creo-run.bat` and your `.java` file (start with `HelloJlink.java`).
2. Open `creo-run.bat` in a text editor and fix the two lines at the top:
   ```bat
   set "CREO_ROOT=C:\Program Files\PTC\Creo 13.4.0.0"
   set "JDK=C:\Program Files\Eclipse Adoptium\jdk-25.0.3+9"
   ```
   `CREO_ROOT` is whatever version directory you have under `C:\Program Files\PTC\`. For `JDK`, point at any JDK you already have — step 3 tells you whether it is new enough.
3. From `cmd`:
   ```
   creo-run.bat --check       verify the environment, launch nothing
   creo-run.bat HelloJlink    compile it and start Creo running it
   ```

`HelloJlink` writes `hello_jlink.log` in the working directory:

```
2026-08-18T04:12:33Z CLASSLOAD java.version=25.0.3 cwd=C:\creo-auto
2026-08-18T04:12:33Z HELLO_START
2026-08-18T04:12:33Z session=true
2026-08-18T04:12:33Z GetCurrentDirectory -> C:\creo-auto
2026-08-18T04:12:33Z HELLO_DONE
```

No log file at all = the applet never ran. Go read the next section.

## The JVM version check — the thing that actually bites

`--check` unzips `otk.jar`, reads the bytecode version of **every** class in it, takes the maximum, and compares that against your JDK.

This is the failure this whole recipe exists for:

> Creo ships its own JVM, and it is often **older** than the JVM `otk.jar` was compiled with. When they mismatch, the J-Link applet dies during class loading. Creo itself starts up perfectly normally. Your app simply never runs — no error, no dialog, no message window. The only trace is `hs_err_pid*.log` files accumulating in the startup directory.

`creo-run.bat` fixes it by setting `JAVA_HOME`, `PRO_JAVA_COMMAND` and prepending `<JDK>\bin\server` to `PATH` before calling `parametric.bat`, so the applet gets your JDK instead of the bundled one.

**Take the maximum, never a sample.** Creo 13.4's `otk.jar` is mixed: 3429 classes at Java 25 and 116 at Java 7. Reading one arbitrary entry reports Java 7 and waves a JDK through that cannot load the jar.

If `--check` says the JDK is too old, install that major version of [Temurin](https://adoptium.net) and repoint `JDK=`.

## What the launcher generates

Rewritten in the working directory on every run — do not hand-edit them:

| File | Contents | Why |
|---|---|---|
| `config.pro` | `add_java_class_path <workdir>\classes` | so Creo can find your compiled class |
| `protk.dat` | `startup java`, `java_app_class <YourClass>`, `java_app_start start`, `java_app_stop stop`, `delay_start false`, `text_dir …` | Creo auto-loads this from the startup directory and registers your app from it |
| `text\msg_app.txt` | a 4-line placeholder message | `text_dir` must contain at least one message file or registration fails |

Then `javac --release 21` into `classes\`, and launch.

**Never put a `protkdat` line in `config.pro`.** It double-registers against the auto-loaded `protk.dat` and Creo hangs behind a modal dialog before the GUI is usable.

## Writing your own app

Copy `HelloJlink.java`, rename the class (class name must equal file name), keep `start()` / `stop()`, and put it next to `creo-run.bat`.

Call the pfc API **by reflection**, as the skeleton does. Two reasons: `javac` never needs `otk.jar` on the classpath, and the class stays loadable across JVM versions.

```java
Object session = callStatic(Class.forName("com.ptc.pfc.pfcGlobal.pfcGlobal"),
                            "GetProESession", new Class<?>[]{}, new Object[]{});
call(session, "ChangeDirectory", "C:\\your\\models");

Object type  = getStatic(Class.forName("com.ptc.pfc.pfcModel.ModelType"), "MDL_PART");
Class<?> pm  = Class.forName("com.ptc.pfc.pfcModel.pfcModel");
Object desc  = callStatic(pm, "ModelDescriptor_Create",
                 new Class<?>[]{ Class.forName("com.ptc.pfc.pfcModel.ModelType"), String.class, String.class },
                 new Object[]{ type, "my-part", null });
Object model = call(session, "RetrieveModel", desc);

Object bom = callStatic(pm, "BOMExportInstructions_Create", new Class<?>[]{}, new Object[]{});
call(model, "Export", "C:\\out\\bom.txt", bom);
```

Notes from real use:

- Log a `_DONE` marker on the last line. It is the only reliable "finished" signal — Creo stays open after your app returns.
- Wrap each export in its own `try`. One part with no solid geometry should not abort the batch.
- Before generating a drawing you must `CreateModelWindow` + `Activate` + `Display` on the model, or you get `XToolkitNotDisplayed`.
- Call `EraseUndisplayedModels` every N parts on long batches or memory climbs.
- Cable/harness objects can hang the drawing generator indefinitely. Keep a skip list.

**Two approaches that do not work** — do not spend an afternoon rediscovering them:

- ❌ **Async mode** (a standalone JVM that starts Creo via `AsyncConnection_Start`). CIP initialization fails on a normal desktop install.
- ❌ **`-g:no_graphics`** with a startup app. Creo exits before reaching `user_initialize`, so the app never registers.

## Driving it from another machine (SSH)

Creo needs a GUI rendering context. An SSH session is non-interactive and has none, so launching Creo directly over SSH always fails. Deliver it into a live interactive session instead:

```
schtasks /create /tn CreoRun /tr C:\path\to\launcher.bat /sc ONCE /st 00:00 /ru <user> /it /f
schtasks /run /tn CreoRun
```

`/it` = run in the interactive session. That session must exist — someone has to be logged on (an RDP session counts, and may be disconnected). Check with `query user`.

`schtasks /run` returns immediately while Creo runs asynchronously, so poll your app's log for its `_DONE` marker rather than waiting on the command.

If the Windows box has WSL, `ssh <wsl-host>` plus WSL interop is far less painful than SSHing into PowerShell. Three rules:

1. **Full paths.** WSL's `PATH` has no `System32`; `schtasks.exe` alone is `command not found`. Use `/mnt/c/Windows/System32/schtasks.exe`.
2. **`</dev/null` on every `.exe`.** Otherwise it consumes the rest of your `bash -s` script as its own stdin and every later command silently does not run.
3. **Pipe output through `iconv`** from the Windows console codepage (`iconv -f GBK -t UTF-8//TRANSLIT` on a Chinese install), or every non-ASCII message is mojibake.

When done, kill `xtop.exe`, `parametric.exe` **and `nmsd.exe`** — the last one is Creo's message daemon, it lingers ~300s and holds a handle on the startup directory, so the directory cannot be deleted or moved until it exits.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Creo starts, app never runs, no log | JVM too old for `otk.jar` — run `creo-run.bat --check` |
| Modal dialog blocks startup | a `protkdat` line snuck into `config.pro` — remove it |
| App fails to register | `text_dir` missing, or the directory has no message file |
| `XToolkitNotDisplayed` | missing `CreateModelWindow` + `Activate` + `Display` before drawing work |
| `DrawingCreateErrors` on some parts | part has no solid geometry — expected, skip it |
| Batch hangs on one model | cable/harness object, or a modal dialog opened behind Creo |
| Working directory "in use", cannot delete | `nmsd.exe` still holding it — wait ~300s or `taskkill /IM nmsd.exe /F` |
| `schtasks /run` does nothing | no interactive session logged in (`query user`) |
| Nothing after some point in an SSH script | an `.exe` without `</dev/null` ate the rest of the script |

## License seats

Most sites have a single seat. A batch run holds it for its whole duration — do not open Creo by hand while automation is running, and always kill the processes when the batch finishes.
