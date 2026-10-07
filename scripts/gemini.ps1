function gemini {
    if (Test-Path "f:\code project\Kimi_Agent_DealForge AI PRD\gemini.env") {
        Get-Content "f:\code project\Kimi_Agent_DealForge AI PRD\gemini.env" | Where-Object { $_ -match '=' } | ForEach-Object {
            $name, $value = $_ -split '=', 2
            [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
    python "f:\code project\Kimi_Agent_DealForge AI PRD\scripts\gemini.py" $args
}
