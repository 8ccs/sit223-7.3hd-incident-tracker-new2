// SIT223/SIT753 7.3HD -- DevOps Pipeline with Jenkins
//
// Seven required stages: Build, Test, Code Quality, Security, Deploy,
// Release, Monitoring. "Checkout" is setup only and is not one of the
// seven. No Docker is installed on this agent, so the artifact is a
// versioned zip rather than a container image; see README.md for the
// reasoning. All later stages read the same artifact -- Release never
// rebuilds the application.
//
// Reference docs used while writing this file:
//   https://www.jenkins.io/doc/book/pipeline/jenkinsfile/
//   https://www.jenkins.io/doc/book/pipeline/docker/ (evaluated, not used -- see README)
//   https://prometheus.io/docs/alerting/latest/overview/

pipeline {
    agent any

    parameters {
        booleanParam(
            name: 'ROLLBACK_PRODUCTION',
            defaultValue: false,
            description: 'When checked, this run rolls production back to the previous released artifact instead of building/deploying/releasing the current commit. Demonstrates the rollback mechanism without rebuilding anything.'
        )
    }

    options {
        timestamps()
        timeout(time: 45, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    // Automatic source-change trigger. This Jenkins instance runs on a
    // personal machine with no public URL, so a GitHub push webhook
    // cannot reach it without an extra tunnel (ngrok/smee), which is
    // unnecessary complexity for a local demo. SCM polling every 5
    // minutes is the practical automatic trigger for this environment;
    // the github-branch-source plugin is already installed so a webhook
    // can be switched on later with no pipeline changes if Jenkins is
    // ever exposed on a public URL.
    triggers {
        pollSCM('H/5 * * * *')
    }

    environment {
        APP_BASE_VERSION = '1.0.0'
        VENV             = "${WORKSPACE}\\.venv"
        ARTIFACT_STORE   = 'C:\\devops-demo\\artifacts'
        // Jenkins runs as the LocalSystem service account, whose PATH does
        // not include the user-level Python install (Python was installed
        // per-user, not machine-wide) -- so "python" alone is not found
        // even though it works in an interactive shell. Every bootstrap
        // step below uses this explicit path instead of relying on PATH.
        SYSTEM_PYTHON    = 'C:\\Users\\auwal\\AppData\\Local\\Programs\\Python\\Python311\\python.exe'
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
                script {
                    env.GIT_COMMIT_SHORT = powershell(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
                }
                echo "Commit ${env.GIT_COMMIT_SHORT} · Jenkins build #${env.BUILD_NUMBER} · job ${env.JOB_NAME}"
            }
        }

        // ---------------------------------------------------------------
        // 1) BUILD -- produce a versioned, deployable artifact
        // ---------------------------------------------------------------
        stage('Build') {
            when { expression { !params.ROLLBACK_PRODUCTION } }
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    if (-not (Test-Path $env:VENV)) {
                        & $env:SYSTEM_PYTHON -m venv $env:VENV
                    }
                    & "$env:VENV\\Scripts\\pip.exe" install --quiet --disable-pip-version-check -r requirements-dev.txt

                    & .\\scripts\\package_artifact.ps1 -Version $env:APP_BASE_VERSION -GitCommit $env:GIT_COMMIT_SHORT -BuildNumber $env:BUILD_NUMBER
                '''
                script {
                    // .trim() alone does not strip a UTF-8 BOM (U+FEFF is
                    // not whitespace to Java/Groovy), so strip it
                    // explicitly as a second line of defence on top of
                    // package_artifact.ps1 writing plain ASCII.
                    env.ARTIFACT_ZIP_NAME = readFile('dist/artifact-name.txt').trim().replace('﻿', '')
                    env.ARTIFACT_ZIP_PATH = readFile('dist/artifact-path.txt').trim().replace('﻿', '')
                    env.FULL_VERSION      = readFile('dist/full-version.txt').trim().replace('﻿', '')
                }
                echo "Built artifact ${env.ARTIFACT_ZIP_NAME} -> version ${env.FULL_VERSION}"
                archiveArtifacts artifacts: 'dist/*.zip, dist/*.json, dist/*.txt', fingerprint: true
            }
        }

        // ---------------------------------------------------------------
        // 2) TEST -- unit + integration tests, coverage, pass/fail gate
        // ---------------------------------------------------------------
        stage('Test') {
            when { expression { !params.ROLLBACK_PRODUCTION } }
            steps {
                powershell '''
                    New-Item -ItemType Directory -Force -Path reports\\test | Out-Null
                    & "$env:VENV\\Scripts\\pytest.exe" tests\\unit tests\\integration `
                        --junitxml=reports\\test\\junit.xml `
                        --cov=app --cov-report=xml:reports\\test\\coverage.xml `
                        --cov-report=html:reports\\test\\htmlcov `
                        --cov-report=term-missing `
                        --cov-fail-under=80
                    exit $LASTEXITCODE
                '''
            }
            post {
                always {
                    junit testResults: 'reports/test/junit.xml', allowEmptyResults: true
                    archiveArtifacts artifacts: 'reports/test/**', allowEmptyArchive: true
                }
            }
        }

        // ---------------------------------------------------------------
        // 3) CODE QUALITY -- maintainability, style, complexity, duplication
        //    (separate from Security; see .flake8 / .pylintrc for gates)
        // ---------------------------------------------------------------
        stage('Code Quality') {
            when { expression { !params.ROLLBACK_PRODUCTION } }
            steps {
                powershell '''
                    # Clean report location for THIS build every time. A
                    # stale reports\\quality left over from an earlier build
                    # (Jenkins reuses the same workspace across builds) must
                    # never be readable by the gate below if a tool in this
                    # run fails before writing its own output.
                    Remove-Item -Recurse -Force reports\\quality -ErrorAction SilentlyContinue
                    New-Item -ItemType Directory -Force -Path reports\\quality | Out-Null

                    & "$env:VENV\\Scripts\\flake8.exe" app --format=default | Out-File reports\\quality\\flake8.txt -Encoding utf8
                    $flake8Exit = $LASTEXITCODE
                    # flake8: 0 = no issues, 1 = issues found -- both are a
                    # normal completed run. Anything else means flake8 itself
                    # failed to execute, which must stop this stage now.
                    if ($flake8Exit -gt 1) {
                        Write-Host "flake8 failed to execute correctly (exit code $flake8Exit)"
                        exit $flake8Exit
                    }

                    & "$env:VENV\\Scripts\\pylint.exe" app --rcfile=.pylintrc | Out-File reports\\quality\\pylint.txt -Encoding utf8
                    # pylint's exit code is a BITMASK of finding categories
                    # (fatal/error/warning/refactor/convention/usage-error),
                    # not a simple 0 = clean / 1 = crash split -- a nonzero
                    # code is completely normal for a real run with
                    # findings, so it is deliberately not gated on here.
                    # scripts\\quality_gate.py's own pylint-score parsing
                    # already fails safely (falls back to a guaranteed-
                    # failing "0.0") if pylint did not produce its usual
                    # "rated at" summary line at all, which is what an
                    # actual crash looks like.
                    $global:LASTEXITCODE = 0
                    & "$env:VENV\\Scripts\\pylint.exe" app --rcfile=.pylintrc --output-format=json | Out-File reports\\quality\\pylint.json -Encoding utf8
                    $global:LASTEXITCODE = 0

                    $scoreLine = Select-String -Path reports\\quality\\pylint.txt -Pattern "rated at (-?\\d+\\.\\d+)/10"
                    if ($scoreLine) { $score = $scoreLine.Matches[0].Groups[1].Value } else { $score = "0.0" }
                    Set-Content -Path reports\\quality\\pylint-score.txt -Value $score -NoNewline

                    & "$env:VENV\\Scripts\\radon.exe" cc app -s -j | Out-File reports\\quality\\radon-cc.json -Encoding utf8
                    $radonCcExit = $LASTEXITCODE
                    # radon has no "findings" exit-code convention like
                    # bandit/flake8 -- 0 is a normal run and anything else is
                    # a genuine error worth stopping on immediately.
                    if ($radonCcExit -ne 0) {
                        Write-Host "radon cc failed to execute correctly (exit code $radonCcExit)"
                        exit $radonCcExit
                    }
                    & "$env:VENV\\Scripts\\radon.exe" mi app -s | Out-File reports\\quality\\radon-mi.txt -Encoding utf8
                    $radonMiExit = $LASTEXITCODE
                    if ($radonMiExit -ne 0) {
                        Write-Host "radon mi failed to execute correctly (exit code $radonMiExit)"
                        exit $radonMiExit
                    }

                    & "$env:VENV\\Scripts\\python.exe" scripts\\quality_gate.py reports\\quality
                    exit $LASTEXITCODE
                '''
            }
            post {
                always {
                    archiveArtifacts artifacts: 'reports/quality/**', allowEmptyArchive: true
                }
            }
        }

        // ---------------------------------------------------------------
        // 4) SECURITY -- SAST (Bandit) + dependency scan (pip-audit)
        //    No container image is produced (Docker-free stack), so
        //    there is no image to scan with Trivy; see README.md.
        // ---------------------------------------------------------------
        stage('Security') {
            when { expression { !params.ROLLBACK_PRODUCTION } }
            steps {
                powershell '''
                    # Clean report location for THIS build every time -- see
                    # the same comment in the Code Quality stage above.
                    Remove-Item -Recurse -Force reports\\security -ErrorAction SilentlyContinue
                    New-Item -ItemType Directory -Force -Path reports\\security | Out-Null

                    & "$env:VENV\\Scripts\\bandit.exe" -r app -f json -o reports\\security\\bandit.json
                    $banditJsonExit = $LASTEXITCODE
                    & "$env:VENV\\Scripts\\bandit.exe" -r app -f txt -o reports\\security\\bandit.txt
                    $banditTxtExit = $LASTEXITCODE
                    # Bandit's own convention: 0 = no issues found, 1 =
                    # issues found at/above the configured threshold -- both
                    # are a normal completed scan; scripts\\security_gate.py
                    # decides pass/fail from the JSON content itself. Any
                    # OTHER exit code means bandit failed to run at all
                    # (crash, bad args, plugin error), which must fail this
                    # stage immediately, before even asking the gate --
                    # otherwise a crashed scanner with a stale or empty
                    # report on disk could look identical to a clean scan.
                    if ($banditJsonExit -gt 1) {
                        Write-Host "bandit (JSON output) failed to execute correctly (exit code $banditJsonExit)"
                        exit $banditJsonExit
                    }
                    if ($banditTxtExit -gt 1) {
                        Write-Host "bandit (text output) failed to execute correctly (exit code $banditTxtExit)"
                        exit $banditTxtExit
                    }

                    & "$env:VENV\\Scripts\\pip-audit.exe" -r requirements.txt -f json -o reports\\security\\pip-audit.json
                    $pipAuditJsonExit = $LASTEXITCODE
                    & "$env:VENV\\Scripts\\pip-audit.exe" -r requirements.txt -f columns | Out-File reports\\security\\pip-audit.txt -Encoding utf8
                    $pipAuditTxtExit = $LASTEXITCODE
                    # Same convention for pip-audit: 0 = clean, 1 =
                    # vulnerabilities found -- both normal; anything else is
                    # a genuine tool failure.
                    if ($pipAuditJsonExit -gt 1) {
                        Write-Host "pip-audit (JSON output) failed to execute correctly (exit code $pipAuditJsonExit)"
                        exit $pipAuditJsonExit
                    }
                    if ($pipAuditTxtExit -gt 1) {
                        Write-Host "pip-audit (text output) failed to execute correctly (exit code $pipAuditTxtExit)"
                        exit $pipAuditTxtExit
                    }

                    & "$env:VENV\\Scripts\\python.exe" scripts\\security_gate.py reports\\security
                    exit $LASTEXITCODE
                '''
            }
            post {
                always {
                    archiveArtifacts artifacts: 'reports/security/**', allowEmptyArchive: true
                }
            }
        }

        // ---------------------------------------------------------------
        // 5) DEPLOY -- staging environment, readiness check, smoke tests
        // ---------------------------------------------------------------
        stage('Deploy') {
            when { expression { !params.ROLLBACK_PRODUCTION } }
            steps {
                powershell '''
                    $ErrorActionPreference = "Stop"
                    & .\\scripts\\deploy.ps1 -Environment staging -ZipPath $env:ARTIFACT_ZIP_PATH
                '''
                powershell '''
                    & "$env:VENV\\Scripts\\python.exe" scripts\\smoke_test.py --base-url http://localhost:5001 --report reports\\deploy\\staging-smoke.json
                    exit $LASTEXITCODE
                '''
            }
            post {
                always {
                    archiveArtifacts artifacts: 'reports/deploy/**', allowEmptyArchive: true
                }
            }
        }

        // ---------------------------------------------------------------
        // 6) RELEASE -- promote the SAME artifact to production
        // ---------------------------------------------------------------
        stage('Release') {
            steps {
                script {
                    if (params.ROLLBACK_PRODUCTION) {
                        powershell '''
                            $ErrorActionPreference = "Stop"
                            & .\\scripts\\rollback.ps1 -Environment production
                        '''
                    } else {
                        powershell '''
                            $ErrorActionPreference = "Stop"
                            & .\\scripts\\deploy.ps1 -Environment production -ZipPath $env:ARTIFACT_ZIP_PATH
                        '''
                    }
                }
                powershell '''
                    New-Item -ItemType Directory -Force -Path reports\\release | Out-Null
                    & "$env:VENV\\Scripts\\python.exe" scripts\\smoke_test.py --base-url http://localhost:5000 --report reports\\release\\production-health.json
                    exit $LASTEXITCODE
                '''
                powershell '''
                    Copy-Item C:\\devops-demo\\production\\current_version.txt reports\\release\\released-version.txt -Force
                '''
            }
            post {
                always {
                    archiveArtifacts artifacts: 'reports/release/**', allowEmptyArchive: true
                }
            }
        }

        // ---------------------------------------------------------------
        // 7) MONITORING -- live metrics + automated alert-path verification
        // ---------------------------------------------------------------
        stage('Monitoring') {
            when { expression { !params.ROLLBACK_PRODUCTION } }
            steps {
                powershell '''
                    New-Item -ItemType Directory -Force -Path reports\\monitoring | Out-Null
                    & "$env:VENV\\Scripts\\python.exe" scripts\\verify_alert_path.py
                    exit $LASTEXITCODE
                '''
            }
            post {
                always {
                    archiveArtifacts artifacts: 'reports/monitoring/**', allowEmptyArchive: true
                }
            }
        }
    }

    post {
        always {
            echo "Pipeline finished for commit ${env.GIT_COMMIT_SHORT ?: 'unknown'} (build #${env.BUILD_NUMBER})."
        }
        success {
            echo "All required stages passed. Version ${env.FULL_VERSION ?: '(rollback run)'} is live in production."
        }
        failure {
            echo 'Pipeline failed -- see the failed stage above. Production was only updated if Release itself completed; check reports/release for the last known-good version and use scripts/rollback.ps1 if needed.'
        }
    }
}
