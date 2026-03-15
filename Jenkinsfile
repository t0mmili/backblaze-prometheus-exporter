currentBuild.rawBuild.project.description = 'Pipeline for building and publishing Backblaze Prometheus Exporter Docker image'

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
      description: 'Docker image tag',
      trim: true
    )
  }

  stages {
    stage('Pre-check') {
      agent any
      when {
        anyOf {
          equals expected: '', actual: dockerRepo
          equals expected: '', actual: dockerCredentials
          equals expected: '', actual: dockerTag
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
    stage('Docker login') {
      steps {
        withCredentials([usernamePassword(
          credentialsId: dockerCredentials,
          usernameVariable: 'DOCKER_USER',
          passwordVariable: 'DOCKER_PASS'
        )]) {
          sh '''
            echo "$DOCKER_PASS" | docker login -u "$DOCKER_USER" --password-stdin
          '''
        }
      }
    }
    stage('Create buildx builder') {
      steps {
        sh '''
          docker buildx create \
            --name jenkins-builder \
            --driver docker-container \
            --driver-opt image=moby/buildkit:v0.15.1 \
            --use || docker buildx use jenkins-builder
          docker buildx inspect --bootstrap
        '''
      }
    }
    stage('Build image') {
      steps {
        sh '''
          docker buildx build \
            --platform linux/amd64 \
            --progress plain \
            -t ${dockerRepo}:${dockerTag} \
            --output type=docker,dest=image.tar,compression=gzip \
            .
        '''
      }
      post {
        success {
          stash includes: 'image.tar', name: 'docker-image'
        }
      }
    }
    stage('Scan image') {
      agent { label 'docker-agent-trivy' }
      steps {
        unstash 'docker-image'

        sh '''
          trivy image \
            --input image.tar \
            --format json \
            --output trivy-results.json \
            --severity HIGH,CRITICAL \
            --exit-code 0 \
            --no-progress
        '''
      }
      post {
        always {
          archiveArtifacts artifacts: 'trivy-results.json', fingerprint: true
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
            tools: [trivy(pattern: 'trivy-results.json')]
          )
        }
      }
    }
    stage('Push image') {
      steps {
        sh '''
          docker buildx build \
            --platform linux/amd64,linux/arm64 \
            --sbom=true \
            -t ${dockerRepo}:${dockerTag} \
            -t ${dockerRepo}:latest \
            --push \
            .
        '''
      }
    }
  }

  post {
    always {
      sh '''
        docker buildx rm jenkins-builder || true
        docker logout || true
      '''
      cleanWs()
    }
    success {
      echo "Image pushed successfully: ${dockerRepo}:${dockerTag}"
    }
  }
}
