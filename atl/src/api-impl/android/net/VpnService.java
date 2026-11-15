package android.net;

import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.IBinder;
import android.os.ParcelFileDescriptor;

import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.Socket;

/**
 * ATL cannot hand an app a tun device, so there is no VPN here. This class only has to EXIST: DuckDuckGo's App Tracking
 * Protection service extends it, and DuckDuckGo's dependency-injection component refers to it at start-up, which died with
 * "NoClassDefFoundError: Failed resolution of: Landroid/net/VpnService;".
 *
 * What an app sees: prepare() has nothing to ask the user, protect() says false, and Builder.establish() returns null,
 * which is what Android itself returns when "the VPN was not prepared or was revoked". The app's VPN feature then simply
 * does not start; everything else in the app works.
 *
 * compat/atl-compat-vpn.jar (tools/make_compat_dex.py) is the same API as a hand-written dex, for an ATL that was built
 * before this file existed. Keep the two in step.
 */
public class VpnService extends Service {

	public static final String SERVICE_INTERFACE = "android.net.VpnService";
	public static final String SERVICE_META_DATA_SUPPORTS_ALWAYS_ON = "android.net.VpnService.SUPPORTS_ALWAYS_ON";

	public static Intent prepare(Context context) {
		return null;
	}

	@Override
	public IBinder onBind(Intent intent) {
		return null;
	}

	public void onRevoke() {
	}

	public boolean protect(int socket) {
		return false;
	}

	public boolean protect(Socket socket) {
		return false;
	}

	public boolean protect(DatagramSocket socket) {
		return false;
	}

	public boolean setUnderlyingNetworks(Network[] networks) {
		return false;
	}

	public boolean isAlwaysOn() {
		return false;
	}

	public boolean isLockdownEnabled() {
		return false;
	}

	public class Builder {

		public Builder() {
		}

		public Builder addAddress(String address, int prefixLength) {
			return this;
		}

		public Builder addAddress(InetAddress address, int prefixLength) {
			return this;
		}

		public Builder addRoute(String address, int prefixLength) {
			return this;
		}

		public Builder addRoute(InetAddress address, int prefixLength) {
			return this;
		}

		public Builder addDnsServer(String address) {
			return this;
		}

		public Builder addDnsServer(InetAddress address) {
			return this;
		}

		public Builder addSearchDomain(String domain) {
			return this;
		}

		public Builder addAllowedApplication(String packageName) {
			return this;
		}

		public Builder addDisallowedApplication(String packageName) {
			return this;
		}

		public Builder allowBypass() {
			return this;
		}

		public Builder allowFamily(int family) {
			return this;
		}

		public Builder setBlocking(boolean blocking) {
			return this;
		}

		public Builder setMetered(boolean metered) {
			return this;
		}

		public Builder setMtu(int mtu) {
			return this;
		}

		public Builder setSession(String session) {
			return this;
		}

		public Builder setConfigureIntent(PendingIntent intent) {
			return this;
		}

		public Builder setUnderlyingNetworks(Network[] networks) {
			return this;
		}

		/** null: "the VPN was not prepared or was revoked" (that is what Android returns in that case). */
		public ParcelFileDescriptor establish() {
			return null;
		}
	}
}
