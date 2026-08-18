import java.io.*;
import java.lang.reflect.*;
import java.time.Instant;

/**
 * Minimal Creo J-Link smoke test + skeleton to copy.
 *
 * Proves the whole chain works: Creo loaded protk.dat, found the class, started
 * a compatible JVM, and handed us a live pfcSession. Writes hello_jlink.log in
 * Creo's startup directory. If that file does not appear, the applet never ran.
 *
 * Every pfc call goes through reflection so this compiles with plain javac and
 * needs otk.jar only at runtime (Creo puts it on the applet classpath itself).
 */
public class HelloJlink {

    static final File LOG = new File(System.getProperty("user.dir"), "hello_jlink.log");

    static { log("CLASSLOAD java.version=" + System.getProperty("java.version")
                 + " cwd=" + System.getProperty("user.dir")); }

    /** Named in protk.dat as java_app_start. */
    public static void start() {
        log("HELLO_START");
        try {
            Object session = callStatic(Class.forName("com.ptc.pfc.pfcGlobal.pfcGlobal"),
                                        "GetProESession", new Class<?>[]{}, new Object[]{});
            log("session=" + (session != null));
            probe(session, "GetCurrentDirectory");
            dumpApi(session);
            log("HELLO_DONE");
        } catch (Throwable t) {
            log("FATAL " + root(t));
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            log(sw.toString());
        }
    }

    /** Named in protk.dat as java_app_stop. Runs when Creo exits. */
    public static void stop() { log("HELLO_STOP"); }

    // ---- logging: never throw, also echo to Creo's console ------------------
    static void log(String m) {
        String line = Instant.now() + " " + m;
        System.out.println("[HelloJlink] " + line);
        try (FileWriter w = new FileWriter(LOG, true)) { w.write(line + "\r\n"); }
        catch (Throwable ignored) { }
    }

    /** Print what you can actually call on this object - pfc's javadoc is not always at hand. */
    static void dumpApi(Object o) {
        java.util.TreeSet<String> names = new java.util.TreeSet<>();
        for (Method m : o.getClass().getMethods()) names.add(m.getName());
        log("session class=" + o.getClass().getName() + " methods=" + names.size());
        log("session api=" + String.join(",", names));
    }

    // ---- reflection helpers: copy these into your own app -------------------
    static void probe(Object o, String name) {
        try { log(name + " -> " + call(o, name)); }
        catch (Throwable t) { log(name + " unavailable (" + root(t) + ")"); }
    }

    /** Instance call with runtime overload resolution. */
    static Object call(Object o, String name, Object... args) throws Exception {
        Method m = find(o.getClass(), name, args);
        m.setAccessible(true);
        return m.invoke(o, args);
    }

    /** Static call. Pass explicit parameter types - pfc factories are overloaded. */
    static Object callStatic(Class<?> c, String name, Class<?>[] types, Object[] args) throws Exception {
        return c.getMethod(name, types).invoke(null, args);
    }

    /** Read a static field, e.g. ModelType.MDL_PART or AssemblyConfiguration.EXPORT_ASM_SINGLE_FILE. */
    static Object getStatic(Class<?> c, String name) throws Exception {
        return c.getField(name).get(null);
    }

    static Method find(Class<?> c, String name, Object[] args) throws NoSuchMethodException {
        for (Method m : c.getMethods()) {
            if (!m.getName().equals(name) || m.getParameterCount() != args.length) continue;
            Class<?>[] p = m.getParameterTypes();
            boolean ok = true;
            for (int i = 0; i < p.length; i++) {
                if (args[i] == null) continue;
                if (!box(p[i]).isAssignableFrom(args[i].getClass())) { ok = false; break; }
            }
            if (ok) return m;
        }
        throw new NoSuchMethodException(c.getName() + "." + name);
    }

    static Class<?> box(Class<?> c) {
        if (!c.isPrimitive()) return c;
        if (c == boolean.class) return Boolean.class;
        if (c == int.class)     return Integer.class;
        if (c == double.class)  return Double.class;
        if (c == long.class)    return Long.class;
        return c;
    }

    /** pfc wraps its exceptions - the innermost cause is the one worth printing. */
    static String root(Throwable t) {
        Throwable u = t;
        while (u.getCause() != null) u = u.getCause();
        return u.getClass().getName() + ":" + u.getMessage();
    }
}
