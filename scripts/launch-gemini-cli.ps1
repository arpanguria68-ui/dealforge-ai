# Load Gemini CLI configuration from gemini.env
if (Test-Path "$PSScriptRoot\gemini.env") {
    Get-Content "$PSScriptRoot\gemini.env" | Where-Object { $_ -match '=' } | ForEach-Object {
        $name, $value = $_ -split '=', 2
        [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
    }
}

# Run the npm gemini CLI
npm exec gemini -- $args
