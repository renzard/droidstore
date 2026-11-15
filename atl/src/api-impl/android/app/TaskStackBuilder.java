package android.app;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.util.Log;
import java.util.ArrayList;

/**
 * Builds the stack of activities that is started for a notification, a shortcut or an "up" navigation.
 *
 * ATL did not have this class, so an app that only touched it died with
 * "NoClassDefFoundError: Failed resolution of: Landroid/app/TaskStackBuilder;" (DuckDuckGo does it at start-up, in
 * AppShortcutCreator.buildBookmarksShortcut). It follows the AOSP class; the only differences come from what ATL's
 * Context has: no Context.startActivities(), so startActivities() starts the intents one after the other.
 */
public class TaskStackBuilder {
	private static final String TAG = "TaskStackBuilder";

	// Intent.FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK | FLAG_ACTIVITY_TASK_ON_HOME
	private static final int ROOT_INTENT_FLAGS = 0x10000000 | 0x00008000 | 0x00004000;

	private final ArrayList<Intent> mIntents = new ArrayList<Intent>();
	private final Context mSourceContext;

	private TaskStackBuilder(Context context) {
		mSourceContext = context;
	}

	public static TaskStackBuilder create(Context context) {
		return new TaskStackBuilder(context);
	}

	public TaskStackBuilder addNextIntent(Intent nextIntent) {
		mIntents.add(nextIntent);
		return this;
	}

	public TaskStackBuilder addNextIntentWithParentStack(Intent nextIntent) {
		ComponentName target = nextIntent.getComponent();
		if (target == null) {
			target = nextIntent.resolveActivity(mSourceContext.getPackageManager());
		}
		if (target != null) {
			addParentStack(target);
		}
		addNextIntent(nextIntent);
		return this;
	}

	public TaskStackBuilder addParentStack(Activity sourceActivity) {
		Intent parent = sourceActivity.getParentActivityIntent();
		if (parent != null) {
			// we have the actual parent intent: build the rest from the manifest, then add the direct parent at the end
			ComponentName target = parent.getComponent();
			if (target == null) {
				target = parent.resolveActivity(mSourceContext.getPackageManager());
			}
			if (target != null) {
				addParentStack(target);
			}
			addNextIntent(parent);
		}
		return this;
	}

	public TaskStackBuilder addParentStack(Class<?> sourceActivityClass) {
		return addParentStack(new ComponentName(mSourceContext, sourceActivityClass));
	}

	public TaskStackBuilder addParentStack(ComponentName sourceActivityName) {
		final int insertAt = mIntents.size();
		PackageManager pm = mSourceContext.getPackageManager();
		try {
			ActivityInfo info = pm.getActivityInfo(sourceActivityName, 0);
			String parentActivity = info.parentActivityName;
			while (parentActivity != null) {
				final ComponentName parent = new ComponentName(info.packageName, parentActivity);
				info = pm.getActivityInfo(parent, 0);
				parentActivity = info.parentActivityName;
				Intent parentIntent = new Intent().setComponent(parent);
				if (parentActivity == null) {
					// the root of the stack is the app's main activity
					parentIntent = Intent.makeMainActivity(parent);
				}
				mIntents.add(insertAt, parentIntent);
			}
		} catch (PackageManager.NameNotFoundException e) {
			Log.e(TAG, "Bad ComponentName while traversing activity parent metadata");
			throw new IllegalArgumentException(e);
		}
		return this;
	}

	public int getIntentCount() {
		return mIntents.size();
	}

	public Intent editIntentAt(int index) {
		return mIntents.get(index);
	}

	public void startActivities() {
		startActivities(null);
	}

	public void startActivities(Bundle options) {
		if (mIntents.isEmpty()) {
			throw new IllegalStateException("No intents added to TaskStackBuilder; cannot startActivities");
		}
		// from the bottom of the stack up, so the last intent ends up on top
		for (Intent intent : getIntents()) {
			mSourceContext.startActivity(intent, options);
		}
	}

	public PendingIntent getPendingIntent(int requestCode, int flags) {
		return getPendingIntent(requestCode, flags, null);
	}

	public PendingIntent getPendingIntent(int requestCode, int flags, Bundle options) {
		if (mIntents.isEmpty()) {
			throw new IllegalStateException("No intents added to TaskStackBuilder; cannot getPendingIntent");
		}
		return PendingIntent.getActivities(mSourceContext, requestCode, getIntents(), flags, options);
	}

	/** The intents to start, bottom of the stack first. The first one starts a new task, the others are copies. */
	public Intent[] getIntents() {
		Intent[] intents = new Intent[mIntents.size()];
		if (intents.length == 0) {
			return intents;
		}
		intents[0] = new Intent(mIntents.get(0)).addFlags(ROOT_INTENT_FLAGS);
		for (int i = 1; i < intents.length; i++) {
			intents[i] = new Intent(mIntents.get(i));
		}
		return intents;
	}
}
