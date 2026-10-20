using System.Runtime.InteropServices;
using ObjCRuntime;

namespace AotProbe;

public sealed record AotProbeResult(string GenericValue, int ExceptionCode, int ProcessId, int SortedChecksum);

public static class DeviceAotProbe
{
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int IntComparer(IntPtr left, IntPtr right);

    [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "getpid")]
    private static extern int GetProcessId();

    [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "qsort")]
    private static extern unsafe void Sort(int* values, nuint count, nuint itemSize, IntComparer comparer);

    public static unsafe AotProbeResult Execute()
    {
        var genericValue = Echo("generic-aot");
        var exceptionCode = ExerciseExceptionPath();
        Span<int> values = stackalloc[] { 4, 1, 3, 2 };
        fixed (int* first = values)
        {
            Sort(first, (nuint)values.Length, (nuint)sizeof(int), Compare);
        }

        return new AotProbeResult(genericValue, exceptionCode, GetProcessId(), values[0] * 1000 + values[3]);
    }

    private static T Echo<T>(T value) => value;

    private static int ExerciseExceptionPath()
    {
        try
        {
            throw new ProbeException(73);
        }
        catch (ProbeException error)
        {
            return error.Code;
        }
    }

    [MonoPInvokeCallback(typeof(IntComparer))]
    private static int Compare(IntPtr left, IntPtr right) => Marshal.ReadInt32(left).CompareTo(Marshal.ReadInt32(right));

    private sealed class ProbeException(int code) : Exception
    {
        public int Code { get; } = code;
    }
}
