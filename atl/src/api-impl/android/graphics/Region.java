package android.graphics;

import android.os.Parcel;
import android.os.Parcelable;

/**
 * Region represents a set of integer-aligned pixels, stored as a collection of
 * (non-overlapping) rectangles. It is backed by a cairo_region_t, which is the
 * same kind of data structure that Skia's SkRegion uses on AOSP.
 */
public class Region implements Parcelable {

	public enum Op {
		DIFFERENCE(0),
		INTERSECT(1),
		UNION(2),
		XOR(3),
		REVERSE_DIFFERENCE(4),
		REPLACE(5);

		Op(int nativeInt) {
			this.nativeInt = nativeInt;
		}

		public final int nativeInt;
	}

	public final long region;

	public Region() {
		this(nativeConstructor());
	}

	public Region(Region other) {
		region = nativeConstructor();
		nativeSetRegion(region, other.region);
	}

	public Region(Rect r) {
		region = nativeConstructor();
		nativeSetRect(region, r.left, r.top, r.right, r.bottom);
	}

	public Region(int left, int top, int right, int bottom) {
		region = nativeConstructor();
		nativeSetRect(region, left, top, right, bottom);
	}

	Region(long region) {
		this.region = region;
	}

	public void setEmpty() {
		nativeSetRect(region, 0, 0, 0, 0);
	}

	public boolean set(Region other) {
		nativeSetRegion(region, other.region);
		return true;
	}

	public boolean set(Rect r) {
		return nativeSetRect(region, r.left, r.top, r.right, r.bottom);
	}

	public boolean set(int left, int top, int right, int bottom) {
		return nativeSetRect(region, left, top, right, bottom);
	}

	public boolean setPath(Path path, Region clip) {
		Path.FillType fillType = path.getFillType();
		return nativeSetPath(region, path.getGskPath(), clip == null ? 0 : clip.region, fillType.fill_rule, fillType.inverse);
	}

	public boolean isEmpty() {
		return nativeIsEmpty(region);
	}

	public boolean isRect() {
		return nativeIsRect(region);
	}

	public boolean isComplex() {
		return nativeIsComplex(region);
	}

	public Rect getBounds() {
		Rect r = new Rect();
		nativeGetBounds(region, r);
		return r;
	}

	public boolean getBounds(Rect r) {
		if (r == null) {
			throw new NullPointerException();
		}
		return nativeGetBounds(region, r);
	}

	public Path getBoundaryPath() {
		Path path = new Path();
		getBoundaryPath(path);
		return path;
	}

	public boolean getBoundaryPath(Path path) {
		int[] rects = nativeGetBoundaryPathRects(region);
		path.rewind();
		for (int i = 0; i < rects.length; i += 4) {
			path.addRect(rects[i], rects[i + 1], rects[i + 2], rects[i + 3], Path.Direction.CW);
		}
		return rects.length > 0;
	}

	public boolean contains(int x, int y) {
		return nativeContains(region, x, y);
	}

	public boolean quickContains(Rect r) {
		return quickContains(r.left, r.top, r.right, r.bottom);
	}

	public boolean quickContains(int left, int top, int right, int bottom) {
		return nativeQuickContains(region, left, top, right, bottom);
	}

	public boolean quickReject(Rect r) {
		return quickReject(r.left, r.top, r.right, r.bottom);
	}

	public boolean quickReject(int left, int top, int right, int bottom) {
		return nativeQuickRejectRect(region, left, top, right, bottom);
	}

	public boolean quickReject(Region other) {
		return nativeQuickRejectRegion(region, other.region);
	}

	public void translate(int dx, int dy) {
		nativeTranslate(region, dx, dy, 0);
	}

	public void translate(int dx, int dy, Region dst) {
		nativeTranslate(region, dx, dy, dst == null ? 0 : dst.region);
	}

	public void scale(float scale) {
		nativeScale(region, scale, 0);
	}

	public void scale(float scale, Region dst) {
		nativeScale(region, scale, dst == null ? 0 : dst.region);
	}

	public final boolean union(Rect r) {
		return op(r, Op.UNION);
	}

	public boolean op(Rect r, Op op) {
		return nativeOpRect(region, r.left, r.top, r.right, r.bottom, op.nativeInt);
	}

	public boolean op(int left, int top, int right, int bottom, Op op) {
		return nativeOpRect(region, left, top, right, bottom, op.nativeInt);
	}

	public boolean op(Region other, Op op) {
		return op(this, other, op);
	}

	public boolean op(Rect rect, Region other, Op op) {
		return nativeOpRegionRect(region, other.region, rect.left, rect.top, rect.right, rect.bottom, op.nativeInt);
	}

	public boolean op(Region region1, Region region2, Op op) {
		return nativeOpRegionRegion(region, region1.region, region2.region, op.nativeInt);
	}

	@Override
	public boolean equals(Object obj) {
		if (obj == null || !(obj instanceof Region)) {
			return false;
		}
		Region other = (Region)obj;
		return nativeEquals(region, other.region);
	}

	@Override
	public String toString() {
		return nativeToString(region);
	}

	@Override
	protected void finalize() throws Throwable {
		try {
			nativeDestructor(region);
		} finally {
			super.finalize();
		}
	}

	public static final Parcelable.Creator<Region> CREATOR = new Parcelable.Creator<Region>() {
		public Region createFromParcel(Parcel p) {
			p.readInt(); /* skip the flattened-size prefix */
			int count = p.readInt();
			long region = nativeConstructor();
			for (int i = 0; i < count; i++) {
				int left = p.readInt();
				int top = p.readInt();
				int right = p.readInt();
				int bottom = p.readInt();
				nativeOpRect(region, left, top, right, bottom, Op.UNION.nativeInt);
			}
			return new Region(region);
		}

		public Region[] newArray(int size) {
			return new Region[size];
		}
	};

	public int describeContents() {
		return 0;
	}

	public void writeToParcel(Parcel p, int flags) {
		int[] rects = nativeGetBoundaryPathRects(region);
		int count = rects.length / 4;
		/* 4 bytes for the rectangle count, 16 bytes (4 ints) per rectangle */
		p.writeInt(4 + rects.length * 4);
		p.writeInt(count);
		for (int i = 0; i < rects.length; i++) {
			p.writeInt(rects[i]);
		}
	}

	private static native long nativeConstructor();
	private static native void nativeDestructor(long region);
	private static native void nativeSetRegion(long dst, long src);
	private static native boolean nativeSetRect(long dst, int left, int top, int right, int bottom);
	private static native boolean nativeSetPath(long dst, long path, long clip, int fill_type, boolean inverse);
	private static native boolean nativeIsEmpty(long region);
	private static native boolean nativeIsRect(long region);
	private static native boolean nativeIsComplex(long region);
	private static native boolean nativeGetBounds(long region, Rect bounds);
	private static native int[] nativeGetBoundaryPathRects(long region);
	private static native boolean nativeContains(long region, int x, int y);
	private static native boolean nativeQuickContains(long region, int left, int top, int right, int bottom);
	private static native boolean nativeQuickRejectRect(long region, int left, int top, int right, int bottom);
	private static native boolean nativeQuickRejectRegion(long region, long other);
	private static native void nativeTranslate(long region, int dx, int dy, long dst);
	private static native void nativeScale(long region, float scale, long dst);
	private static native boolean nativeOpRect(long dst, int left, int top, int right, int bottom, int op);
	private static native boolean nativeOpRegionRect(long dst, long other, int left, int top, int right, int bottom, int op);
	private static native boolean nativeOpRegionRegion(long dst, long region1, long region2, int op);
	private static native boolean nativeEquals(long region1, long region2);
	private static native String nativeToString(long region);
}
