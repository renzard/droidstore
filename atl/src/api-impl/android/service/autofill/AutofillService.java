package android.service.autofill;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

/**
 * ATL has no autofill framework, so nothing ever binds to or calls an autofill service. The class only has to EXIST:
 * DuckDuckGo's autofill service extends it, and DuckDuckGo's dependency-injection component refers to it at start-up,
 * which died with "NoClassDefFoundError: Failed resolution of: Landroid/service/autofill/AutofillService;".
 *
 * The abstract onFillRequest/onSaveRequest of the real class are left out: their parameter classes (FillRequest,
 * FillCallback, ...) do not exist in ATL either, and the app's subclass defines them itself.
 *
 * compat/atl-compat-autofill.jar (tools/make_compat_dex.py) is the same API as a hand-written dex, for an ATL that was
 * built before this file existed. Keep the two in step.
 */
public class AutofillService extends Service {

	public static final String SERVICE_INTERFACE = "android.service.autofill.AutofillService";
	public static final String SERVICE_META_DATA = "android.autofill";

	@Override
	public IBinder onBind(Intent intent) {
		return null;
	}

	public void onConnected() {
	}

	public void onDisconnect() {
	}
}
