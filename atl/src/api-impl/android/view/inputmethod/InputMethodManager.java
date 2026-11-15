package android.view.inputmethod;

import android.os.IBinder;
import android.view.View;
import android.widget.EditText;
import java.util.ArrayList;
import java.util.List;

public class InputMethodManager {

	private static long im_context = nativeInit();
	private static View activeView = null;
	private static EditText focusedEditText = null; // last EditText that asked for the keyboard (for toggleSoftInput)

	private ArrayList<InputMethodInfo> input_method_list = new ArrayList<InputMethodInfo>();

	public boolean hideSoftInputFromWindow(IBinder windowToken, int flags) {
		nativeHideSoftInput(im_context);
		activeView = null;
		return false;
	}

	public boolean showSoftInput(View view, int flags) {
		if (view == activeView) {
			return nativeShowSoftInput(im_context, view.widget, null, 0);
		}
		if (view instanceof EditText)
			focusedEditText = (EditText)view;
		EditorInfo outAttrs = new EditorInfo();
		InputConnection ic = view.onCreateInputConnection(outAttrs);
		if (ic != null) {
			activeView = view;
			return nativeShowSoftInput(im_context, view.widget, ic, outAttrs.inputType);
		}
		// A plain EditText is a native GTK text field and has no InputConnection. Apps that call showSoftInput()
		// after requestFocus() (search boxes, dialogs, login screens) got no keyboard at all, because tapping is the
		// only thing GTK reacts to by itself.
		if (view instanceof EditText) {
			boolean shown = nativeShowSoftInputEditable(im_context, view.widget, ((EditText)view).getInputType());
			if (shown)
				activeView = view;
			return shown;
		}
		return false;
	}

	public void toggleSoftInput(int showFlags, int hideFlags) {
		if (activeView != null)
			hideSoftInputFromWindow(null, hideFlags);
		else if (focusedEditText != null)
			showSoftInput(focusedEditText, showFlags);
	}

	public void showSoftInputFromInputMethod(IBinder token, int flags) {}

	public void hideSoftInputFromInputMethod(IBinder token, int flags) {
		hideSoftInputFromWindow(token, flags);
	}

	public boolean isAcceptingText() {
		return activeView != null;
	}

	public boolean isFullscreenMode() { return false; }

	public boolean isActive(View view) {
		return activeView == view;
	}

	public List<InputMethodInfo> getEnabledInputMethodList() {
		return input_method_list;
	}

	public List<InputMethodInfo> getInputMethodList() {
		return input_method_list;
	}

	public void restartInput(View view) {}

	public void updateSelection(View view, int selStart, int selEnd, int candidatesStart, int candidatesEnd) {}

	public InputMethodSubtype getCurrentInputMethodSubtype() {
		return new InputMethodSubtype();
	}

	private static native long nativeInit();
	private native boolean nativeShowSoftInput(long im_context, long widget, InputConnection ic, int inputType);
	private native boolean nativeShowSoftInputEditable(long im_context, long widget, int inputType);
	private native void nativeHideSoftInput(long im_context);
}
