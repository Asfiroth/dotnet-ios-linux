# Runtime patch set

`0001-linux-host-apple-mobile-aot-cross.patch` applies only to dotnet/runtime
`4271d88e0aebf3d04f188f1334c2220d80555ef6` (`v10.0.12`).

The patch does three narrowly scoped things:

1. Selects Mach-O assembly sections from the configured target instead of the
   Linux preprocessor identity of the host compiler.
2. Excludes Apple runtime trampoline allocation from a cross-compiler process;
   no Mach runtime API is needed while compiling managed assemblies.
3. Removes an unused `mach-o/dyld.h` include from code that otherwise uses the
   portable `dlopen` interface.

It does not provide fake Darwin declarations and does not alter generated ARM64
instructions, ABI offsets, or the target runtime libraries.
