package httpapi

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

func TestGeneratedOpenAPICodeHasNoDrift(t *testing.T) {
	moduleRoot, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatalf("resolve module root: %v", err)
	}
	temporaryOutput := filepath.Join(t.TempDir(), "openapi.gen.go")
	configuration := fmt.Sprintf(`package: httpapi
generate:
  models: true
  chi-server: true
  strict-server: true
  embedded-spec: true
output: %q
`, filepath.ToSlash(temporaryOutput))
	configurationPath := filepath.Join(t.TempDir(), "oapi-codegen.yaml")
	if err := os.WriteFile(configurationPath, []byte(configuration), 0o600); err != nil {
		t.Fatalf("write temporary generation config: %v", err)
	}

	goExecutable := filepath.Join(runtime.GOROOT(), "bin", "go")
	if runtime.GOOS == "windows" {
		goExecutable += ".exe"
	}
	command := exec.Command(goExecutable, "tool", "oapi-codegen", "--config="+configurationPath, filepath.Join(moduleRoot, "..", "..", "specs", "openapi.yaml"))
	command.Dir = moduleRoot
	command.Env = append(os.Environ(), "GOTOOLCHAIN=local", "CGO_ENABLED=0")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("run pinned oapi-codegen: %v\n%s", err, output)
	}

	want, err := os.ReadFile("openapi.gen.go")
	if err != nil {
		t.Fatalf("read checked-in generated code: %v", err)
	}
	got, err := os.ReadFile(temporaryOutput)
	if err != nil {
		t.Fatalf("read temporary generated code: %v", err)
	}
	if !bytes.Equal(got, want) {
		t.Fatal("generated OpenAPI code has drift; run go generate ./internal/httpapi")
	}
}
