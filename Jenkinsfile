pipeline {
  agent { label 'docker-agent-general' }

  options {
    buildDiscarder logRotator(
      artifactDaysToKeepStr: '',
      artifactNumToKeepStr: '',
      daysToKeepStr: '',
      numToKeepStr: '5'
    )
    skipDefaultCheckout true
  }

  parameters {
    booleanParam(
      name: 'dryRun',
      defaultValue: true,
      description: 'Build and scan only. Nothing is pushed to Docker Hub.'
    )
    booleanParam(
      name: 'pushLatest',
      defaultValue: false,
      description: 'Tag as latest (ignored when dryRun is set)'
    )
    string(
      name: 'dockerRepo',
      defaultValue: 't0mmili/backblaze-prometheus-exporter',
      description: 'Docker Hub repository',
      trim: true
    )
    string(
      name: 'dockerCredentials',
      defaultValue: 'docker-hub-creds',
      description: 'Jenkins credentials ID for Docker Hub',
      trim: true
    )
    string(
      name: 'dockerTag',
      defaultValue: '',
      description: 'Image tag. Leave empty to use the version from pyproject.toml.',
      trim: true
    )
  }

  environment {
    BUILDER  = "jenkins-builder-${env.BUILD_NUMBER}"
    CI_IMAGE = "bbe-ci:${env.BUILD_NUMBER}"
  }

  stages {
    stage('Pre-check') {
      agent any
      when {
        anyOf {
          equals expected: '', actual: dockerRepo
          equals expected: '', actual: dockerCredentials
        }
      }
      steps {
        error 'One or more required job parameters are empty.'
      }
      post {
        cleanup {
          cleanWs()
        }
      }
    }

    stage('Checkout') {
      steps {
        checkout scm
      }
    }

    stage('Resolve version') {
      steps {
        script {
          def declared = sh(
            returnStdout: true,
            script: "grep -m1 '^version = ' pyproject.toml | cut -d'\"' -f2"
          ).trim()

          if (!declared) {
            error 'Could not read version from pyproject.toml'
          }

          env.APP_VERSION = declared
          env.IMAGE_TAG = params.dockerTag ?: declared

          echo "version=${env.APP_VERSION} tag=${env.IMAGE_TAG}"
        }
      }
    }

    stage('Validate') {
      parallel {
        stage('Dockerfile lint') {
          steps {
            sh 'docker run --rm -i hadolint/hadolint hadolint -f codeclimate -t warning - < Dockerfile'
          }
        }
      }
    }

    stage('Build images') {
      steps {
        sh '''
          docker buildx create \
            --name "${BUILDER}" \
            --driver docker-container \
            --driver-opt image=moby/buildkit:v0.15.1 \
            --bootstrap
        '''

        sh '''
          for platform in amd64 arm64; do
            docker buildx build \
              --builder "${BUILDER}" \
              --platform "linux/${platform}" \
              --build-arg APP_VERSION="${APP_VERSION}" \
              --provenance=false \
              --sbom=false \
              --progress plain \
              --load \
              -t "${CI_IMAGE}-${platform}" \
              .
            docker save "${CI_IMAGE}-${platform}" -o "image-${platform}.tar"
          done
        '''
      }
      post {
        success {
          stash includes: 'image-*.tar', name: 'docker-images'
        }
      }
    }

    stage('Scan images') {
      agent { label 'docker-agent-trivy' }
      steps {
        unstash 'docker-images'

        sh '''
          for platform in amd64 arm64; do
            trivy image \
              --input "image-${platform}.tar" \
              --format json \
              --output "trivy-results-${platform}.json" \
              --severity HIGH,CRITICAL \
              --exit-code 0 \
              --no-progress
          done
        '''
      }
      post {
        always {
          archiveArtifacts artifacts: 'trivy-results-*.json', fingerprint: true
          recordIssues(
            enabledForFailure: true,
            qualityGates: [
              // Fail if even ONE NEW Critical (Error) is introduced
              [criticality: 'FAILURE', integerThreshold: 1, type: 'NEW_ERROR'],
              // Fail if > than 5 NEW Highs (High) are introduced
              [criticality: 'FAILURE', integerThreshold: 5, type: 'NEW_HIGH'],
              // Ustable if there are any TOTAL Criticals (Error)
              [criticality: 'UNSTABLE', integerThreshold: 1, type: 'TOTAL_ERROR']
            ],
            tools: [trivy(pattern: 'trivy-results-*.json')]
          )
        }
      }
    }

    stage('Publish') {
      when {
        expression { !params.dryRun }
      }
      steps {
        withCredentials([usernamePassword(
          credentialsId: dockerCredentials,
          usernameVariable: 'DOCKER_USER',
          passwordVariable: 'DOCKER_PASS'
        )]) {
          sh 'echo "$DOCKER_PASS" | docker login -u "$DOCKER_USER" --password-stdin'
        }

        script {
          def tags = ["-t ${params.dockerRepo}:${env.IMAGE_TAG}"]
          if (params.pushLatest) {
            tags << "-t ${params.dockerRepo}:latest"
          }
          sh  """
            docker buildx build \
              --builder "${BUILDER}" \
              --platform "linux/amd64,linux/arm64" \
              --build-arg APP_VERSION="${APP_VERSION}" \
              --provenance=true \
              --sbom=true \
              --progress plain \
              ${tags.join(' ')} \
              --push \
              .
          """
        }

        sh 'docker buildx imagetools inspect "${dockerRepo}:${IMAGE_TAG}"'
      }
    }
  }

  post {
    always {
      sh '''
        docker buildx rm "${BUILDER}" >/dev/null 2>&1 || true
        docker rmi -f "${CI_IMAGE}-amd64" "${CI_IMAGE}-arm64" >/dev/null 2>&1 || true
        docker logout || true
      '''
      cleanWs()
    }
    success {
      script {
        if (params.dryRun) {
          echo "Dry Run successful. Built and scanned ${params.dockerRepo}:${env.IMAGE_TAG}. Nothing published."
        } else {
          echo "Published successfully ${params.dockerRepo}:${env.IMAGE_TAG}"
        }
      }
    }
  }
}
