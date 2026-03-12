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
  }

  parameters {
    string(
        defaultValue: '',
        description: 'Git repo url',
        name: 'gitUrl',
        trim: true
    )
    string(
        defaultValue: '',
        description: 'Git repo branch',
        name: 'gitBranch',
        trim: true
    )
    string(
        defaultValue: '',
        description: 'Git credentials id',
        name: 'gitCredentials',
        trim: true
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
        // checkout([$class: 'GitSCM', branches: [[name: '*/$gitBranch']], extensions: [[$class: 'LocalBranch', localBranch: gitBranch]], userRemoteConfigs: [[credentialsId: gitCredentials, url: gitUrl]]])
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
            -t ${dockerRepo}:${dockerTag} \
            --load \
            .
        '''
      }
    }
    stage('Scan image') {
      agent { label 'docker-agent-trivy' }
      steps {
        sh """
          trivy image \
            --format json \
            --output trivy-results.json \
            --severity HIGH,CRITICAL \
            --exit-code 0 \
            --no-progress \
            ${dockerRepo}:${dockerTag}
        """
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
      sh 'docker logout || true'
      cleanWs()
    }
    success {
      echo "Image pushed successfully: ${dockerRepo}:${dockerTag}"
    }
  }
}
