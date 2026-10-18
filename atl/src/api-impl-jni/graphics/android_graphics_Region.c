#include <math.h>

#include <gdk/gdk.h>
#include <gsk/gsk.h>

#include "../defines.h"

#include "../generated_headers/android_graphics_Region.h"

#ifndef MAX
	#define MAX(a, b) ((a) > (b) ? (a) : (b))
#endif
#ifndef MIN
	#define MIN(a, b) ((a) < (b) ? (a) : (b))
#endif

/* must match android.graphics.Region.Op */
#define REGION_OP_DIFFERENCE         0
#define REGION_OP_INTERSECT          1
#define REGION_OP_UNION              2
#define REGION_OP_XOR                3
#define REGION_OP_REVERSE_DIFFERENCE 4
#define REGION_OP_REPLACE            5

static inline cairo_region_t *to_region(jlong ptr)
{
	return (cairo_region_t *)_PTR(ptr);
}

static void region_clear(cairo_region_t *region)
{
	cairo_rectangle_int_t empty = {0, 0, 0, 0};
	cairo_region_intersect_rectangle(region, &empty);
}

static void region_assign(cairo_region_t *dst, const cairo_region_t *src)
{
	if (dst == src)
		return;
	region_clear(dst);
	cairo_region_union(dst, src);
}

static void region_set_rect(cairo_region_t *region, int left, int top, int right, int bottom)
{
	region_clear(region);
	if (right > left && bottom > top) {
		cairo_rectangle_int_t rect = {left, top, right - left, bottom - top};
		cairo_region_union_rectangle(region, &rect);
	}
}

static cairo_region_t *region_from_rect(int left, int top, int right, int bottom)
{
	cairo_region_t *region = cairo_region_create();
	if (right > left && bottom > top) {
		cairo_rectangle_int_t rect = {left, top, right - left, bottom - top};
		cairo_region_union_rectangle(region, &rect);
	}
	return region;
}

/*
 * Sets dst to the result of "a <op> b". The result is computed in a temporary
 * region so that dst may alias a and/or b.
 */
static void region_op(cairo_region_t *dst, const cairo_region_t *a, const cairo_region_t *b, int op)
{
	cairo_region_t *result;

	if (op == REGION_OP_REVERSE_DIFFERENCE) {
		/* b - a */
		result = cairo_region_copy(b);
		cairo_region_subtract(result, a);
	} else if (op == REGION_OP_REPLACE) {
		result = cairo_region_copy(b);
	} else {
		result = cairo_region_copy(a);
		switch (op) {
			case REGION_OP_DIFFERENCE:
				cairo_region_subtract(result, b);
				break;
			case REGION_OP_INTERSECT:
				cairo_region_intersect(result, b);
				break;
			case REGION_OP_UNION:
				cairo_region_union(result, b);
				break;
			case REGION_OP_XOR:
				cairo_region_xor(result, b);
				break;
		}
	}

	region_assign(dst, result);
	cairo_region_destroy(result);
}

JNIEXPORT jlong JNICALL Java_android_graphics_Region_nativeConstructor(JNIEnv *env, jclass class)
{
	return _INTPTR(cairo_region_create());
}

JNIEXPORT void JNICALL Java_android_graphics_Region_nativeDestructor(JNIEnv *env, jclass class, jlong region)
{
	if (region)
		cairo_region_destroy(to_region(region));
}

JNIEXPORT void JNICALL Java_android_graphics_Region_nativeSetRegion(JNIEnv *env, jclass class, jlong dst, jlong src)
{
	region_assign(to_region(dst), to_region(src));
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeSetRect(JNIEnv *env, jclass class, jlong dst, jint left, jint top, jint right, jint bottom)
{
	cairo_region_t *region = to_region(dst);
	region_set_rect(region, left, top, right, bottom);
	return !cairo_region_is_empty(region);
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeSetPath(JNIEnv *env, jclass class, jlong dst_ptr, jlong path_ptr, jlong clip_ptr, jint fill_rule, jboolean inverse)
{
	cairo_region_t *dst = to_region(dst_ptr);
	GskPath *path = _PTR(path_ptr);
	cairo_region_t *clip = clip_ptr ? to_region(clip_ptr) : NULL;

	region_clear(dst);

	if (clip && cairo_region_is_empty(clip))
		return FALSE;

	int left, top, right, bottom;

	if (inverse) {
		/* the inverse fill extends to the clip, so rasterize the whole clip */
		cairo_rectangle_int_t extents;
		if (!clip)
			return FALSE;
		cairo_region_get_extents(clip, &extents);
		left = extents.x;
		top = extents.y;
		right = extents.x + extents.width;
		bottom = extents.y + extents.height;
	} else {
		graphene_rect_t bounds;
		if (!gsk_path_get_bounds(path, &bounds))
			return FALSE;

		left = (int)floorf(bounds.origin.x);
		top = (int)floorf(bounds.origin.y);
		right = (int)ceilf(bounds.origin.x + bounds.size.width);
		bottom = (int)ceilf(bounds.origin.y + bounds.size.height);

		if (clip) {
			cairo_rectangle_int_t extents;
			cairo_region_get_extents(clip, &extents);
			left = MAX(left, extents.x);
			top = MAX(top, extents.y);
			right = MIN(right, extents.x + extents.width);
			bottom = MIN(bottom, extents.y + extents.height);
		}
	}

	if (right <= left || bottom <= top)
		return FALSE;

	cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_A8, right - left, bottom - top);
	if (cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) {
		cairo_surface_destroy(surface);
		return FALSE;
	}

	cairo_t *cr = cairo_create(surface);
	cairo_translate(cr, -left, -top);
	cairo_set_fill_rule(cr, fill_rule);

	if (inverse) {
		/* fill everything, then punch out the path */
		cairo_set_source_rgba(cr, 1, 1, 1, 1);
		cairo_paint(cr);
		cairo_set_operator(cr, CAIRO_OPERATOR_CLEAR);
		gsk_path_to_cairo(path, cr);
		cairo_fill(cr);
	} else {
		gsk_path_to_cairo(path, cr);
		cairo_set_source_rgba(cr, 1, 1, 1, 1);
		cairo_fill(cr);
	}

	cairo_destroy(cr);
	cairo_surface_flush(surface);

	cairo_region_t *raster = gdk_cairo_region_create_from_surface(surface);
	cairo_surface_destroy(surface);

	if (!raster)
		return FALSE;

	cairo_region_translate(raster, left, top);
	if (clip)
		cairo_region_intersect(raster, clip);
	region_assign(dst, raster);
	cairo_region_destroy(raster);

	return !cairo_region_is_empty(dst);
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeIsEmpty(JNIEnv *env, jclass class, jlong region)
{
	return cairo_region_is_empty(to_region(region));
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeIsRect(JNIEnv *env, jclass class, jlong region)
{
	/* the empty region is not considered a rectangle */
	return cairo_region_num_rectangles(to_region(region)) == 1;
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeIsComplex(JNIEnv *env, jclass class, jlong region)
{
	return cairo_region_num_rectangles(to_region(region)) > 1;
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeGetBounds(JNIEnv *env, jclass class, jlong region_ptr, jobject bounds)
{
	cairo_region_t *region = to_region(region_ptr);
	cairo_rectangle_int_t extents;

	if (cairo_region_is_empty(region)) {
		_SET_INT_FIELD(bounds, "left", 0);
		_SET_INT_FIELD(bounds, "top", 0);
		_SET_INT_FIELD(bounds, "right", 0);
		_SET_INT_FIELD(bounds, "bottom", 0);
		return FALSE;
	}

	cairo_region_get_extents(region, &extents);
	_SET_INT_FIELD(bounds, "left", extents.x);
	_SET_INT_FIELD(bounds, "top", extents.y);
	_SET_INT_FIELD(bounds, "right", extents.x + extents.width);
	_SET_INT_FIELD(bounds, "bottom", extents.y + extents.height);
	return TRUE;
}

JNIEXPORT jintArray JNICALL Java_android_graphics_Region_nativeGetBoundaryPathRects(JNIEnv *env, jclass class, jlong region_ptr)
{
	cairo_region_t *region = to_region(region_ptr);
	int count = cairo_region_num_rectangles(region);
	jintArray array = (*env)->NewIntArray(env, count * 4);

	if (!array)
		return NULL;

	if (count > 0) {
		jint *values = (*env)->GetIntArrayElements(env, array, NULL);
		for (int i = 0; i < count; i++) {
			cairo_rectangle_int_t rect;
			cairo_region_get_rectangle(region, i, &rect);
			values[i * 4 + 0] = rect.x;
			values[i * 4 + 1] = rect.y;
			values[i * 4 + 2] = rect.x + rect.width;
			values[i * 4 + 3] = rect.y + rect.height;
		}
		(*env)->ReleaseIntArrayElements(env, array, values, 0);
	}

	return array;
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeContains(JNIEnv *env, jclass class, jlong region, jint x, jint y)
{
	return cairo_region_contains_point(to_region(region), x, y);
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeQuickContains(JNIEnv *env, jclass class, jlong region_ptr, jint left, jint top, jint right, jint bottom)
{
	cairo_region_t *region = to_region(region_ptr);

	if (right <= left || bottom <= top)
		return FALSE;
	if (cairo_region_num_rectangles(region) > 1)
		return FALSE;

	cairo_rectangle_int_t rect = {left, top, right - left, bottom - top};
	return cairo_region_contains_rectangle(region, &rect) == CAIRO_REGION_OVERLAP_IN;
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeQuickRejectRect(JNIEnv *env, jclass class, jlong region_ptr, jint left, jint top, jint right, jint bottom)
{
	cairo_region_t *region = to_region(region_ptr);

	if (right <= left || bottom <= top)
		return TRUE;
	if (cairo_region_is_empty(region))
		return TRUE;

	cairo_rectangle_int_t rect = {left, top, right - left, bottom - top};
	return cairo_region_contains_rectangle(region, &rect) == CAIRO_REGION_OVERLAP_OUT;
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeQuickRejectRegion(JNIEnv *env, jclass class, jlong region_ptr, jlong other_ptr)
{
	cairo_region_t *region = to_region(region_ptr);
	cairo_region_t *other = to_region(other_ptr);
	cairo_rectangle_int_t extents;

	if (cairo_region_is_empty(region) || cairo_region_is_empty(other))
		return TRUE;

	cairo_region_get_extents(other, &extents);
	return cairo_region_contains_rectangle(region, &extents) == CAIRO_REGION_OVERLAP_OUT;
}

JNIEXPORT void JNICALL Java_android_graphics_Region_nativeTranslate(JNIEnv *env, jclass class, jlong src_ptr, jint dx, jint dy, jlong dst_ptr)
{
	cairo_region_t *src = to_region(src_ptr);

	if (!dst_ptr) {
		cairo_region_translate(src, dx, dy);
		return;
	}

	cairo_region_t *dst = to_region(dst_ptr);
	region_assign(dst, src);
	cairo_region_translate(dst, dx, dy);
}

JNIEXPORT void JNICALL Java_android_graphics_Region_nativeScale(JNIEnv *env, jclass class, jlong src_ptr, jfloat scale, jlong dst_ptr)
{
	cairo_region_t *src = to_region(src_ptr);
	cairo_region_t *dst = dst_ptr ? to_region(dst_ptr) : src;
	cairo_region_t *result;
	int count;

	if (scale <= 0) {
		region_clear(dst);
		return;
	}

	result = cairo_region_create();
	count = cairo_region_num_rectangles(src);

	for (int i = 0; i < count; i++) {
		cairo_rectangle_int_t rect;
		cairo_region_get_rectangle(src, i, &rect);

		int left = (int)roundf(rect.x * scale);
		int top = (int)roundf(rect.y * scale);
		int right = (int)roundf((rect.x + rect.width) * scale);
		int bottom = (int)roundf((rect.y + rect.height) * scale);

		if (right > left && bottom > top) {
			cairo_rectangle_int_t scaled = {left, top, right - left, bottom - top};
			cairo_region_union_rectangle(result, &scaled);
		}
	}

	region_assign(dst, result);
	cairo_region_destroy(result);
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeOpRect(JNIEnv *env, jclass class, jlong dst_ptr, jint left, jint top, jint right, jint bottom, jint op)
{
	cairo_region_t *dst = to_region(dst_ptr);
	cairo_region_t *rect = region_from_rect(left, top, right, bottom);

	region_op(dst, dst, rect, op);
	cairo_region_destroy(rect);

	return !cairo_region_is_empty(dst);
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeOpRegionRect(JNIEnv *env, jclass class, jlong dst_ptr, jlong other_ptr, jint left, jint top, jint right, jint bottom, jint op)
{
	cairo_region_t *dst = to_region(dst_ptr);
	cairo_region_t *other = to_region(other_ptr);
	cairo_region_t *rect = region_from_rect(left, top, right, bottom);

	region_op(dst, rect, other, op);
	cairo_region_destroy(rect);

	return !cairo_region_is_empty(dst);
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeOpRegionRegion(JNIEnv *env, jclass class, jlong dst_ptr, jlong region1_ptr, jlong region2_ptr, jint op)
{
	cairo_region_t *dst = to_region(dst_ptr);

	region_op(dst, to_region(region1_ptr), to_region(region2_ptr), op);

	return !cairo_region_is_empty(dst);
}

JNIEXPORT jboolean JNICALL Java_android_graphics_Region_nativeEquals(JNIEnv *env, jclass class, jlong region1, jlong region2)
{
	return cairo_region_equal(to_region(region1), to_region(region2));
}

JNIEXPORT jstring JNICALL Java_android_graphics_Region_nativeToString(JNIEnv *env, jclass class, jlong region_ptr)
{
	cairo_region_t *region = to_region(region_ptr);
	int count = cairo_region_num_rectangles(region);
	GString *string = g_string_new("Region(");
	jstring result;

	for (int i = 0; i < count; i++) {
		cairo_rectangle_int_t rect;
		cairo_region_get_rectangle(region, i, &rect);
		g_string_append_printf(string, "%s[%d,%d,%d,%d]", i ? " " : "", rect.x, rect.y, rect.x + rect.width, rect.y + rect.height);
	}

	g_string_append_c(string, ')');

	result = _JSTRING(string->str);
	g_string_free(string, TRUE);
	return result;
}
